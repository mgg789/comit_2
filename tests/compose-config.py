#!/usr/bin/env python3
"""Validate Compose modes using only synthetic values; never read project .env secrets."""
import json,os,subprocess,base64,re
from pathlib import Path
r=Path(__file__).resolve().parents[1]
env={'PATH':os.environ['PATH'],'HOME':os.environ['HOME']}
required=['POSTGRES_PASSWORD','AUTH_SECRET','AUTH_ALLOWED_ORIGINS','PUBLIC_SITE_URL','TRUST_PROXY','CANDIDATE_PIPELINE_TOKEN','AGENTS_SERVICE_TOKEN','VERIFICATION_ENGINE_TOKEN','VERIFICATION_DOCUMENT_KEY','TESTING_ENGINE_TOKEN','CHAT_SERVICE_TOKEN','CHAT_DEVICE_SECRET','CHAT_SIGNING_KEY','QGRAMM_MANAGEMENT_SECRET','QGRAMM_MASTER_KEY','QGRAMM_HPKE_KEY','SMTP_USER','SMTP_PASSWORD','DEEPSEEK_API_KEY','JEV_API_KEY','JEV_LOCAL_URL']
for k in required:env[k]='synthetic-config-validation-only-123456789'
for k in ['CHAT_DEVICE_SECRET','CHAT_SIGNING_KEY','QGRAMM_MASTER_KEY','QGRAMM_HPKE_KEY','VERIFICATION_DOCUMENT_KEY']:env[k]=base64.b64encode(b'x'*32).decode()
env.update(AUTH_ALLOWED_ORIGINS='https://comit.test',PUBLIC_SITE_URL='https://comit.test',TRUST_PROXY='172.16.0.0/12',JEV_LOCAL_URL='http://laya.test:12444')
def cfg(file,dev=False,values=env):
 args=['docker','compose','--env-file','/dev/null','-f',file]
 if dev:args+=['--profile','dev']
 return json.loads(subprocess.check_output(args+['config','--format','json'],env=values,cwd=r))
p=cfg('infra/docker-compose.yml');compat=cfg('infra/docker-compose.prod.yml');assert p==compat
s=p['services'];assert s['testing-engine']['environment']['TESTING_API_URL']=='http://api:8080';assert s['testing-engine']['environment']['TESTING_DATA_DIR']=='/data/testing';assert s['api']['environment']['NODE_ENV']=='production';assert s['api']['environment']['DEV_AUTH_EXPOSE_CODE']=='false'
assert s['integration-engine']['environment']['APP_ENV']=='production'
for n in ['api','integration-engine','verification-engine']:assert s[n]['environment']['FSP_DEMO_ENABLED']=='false'
for n in ['auth-mail-worker','candidate-profile-worker','recommendation-worker','candidate-worker','vacancy-worker','combine-worker','rerank-worker','feed-refill-worker','preference-worker','agents-worker','organization-agent-worker']:assert n in s,n
assert set(p['volumes'])=={'pgdata','harrier_cache','chat-data','testing-data'}
assert s['auth-migrate']['depends_on']['agents-migrate']['condition']=='service_completed_successfully'
assert s['agents-migrate']['depends_on']['candidate-migrate']['condition']=='service_completed_successfully'
assert s['auth-mail-worker']['environment']['PUBLIC_SITE_URL']=='https://comit.test'
for n in ['api','auth-mail-worker']:
 assert 'host.docker.internal=host-gateway' in s[n]['extra_hosts']
 assert s[n]['environment']['SMTP_HOST']=='host.docker.internal'
 assert s[n]['environment']['SMTP_PORT']=='2525'
 assert s[n]['environment']['SMTP_TLS_SERVERNAME']=='mail.droidje.com'
dev_values={'PATH':os.environ['PATH'],'HOME':os.environ['HOME'],'CODE_JUDGE_URL':'http://isolated-judge.test:8106','CODE_JUDGE_TOKEN':'synthetic-config-only-judge-token'}
d=cfg('infra/docker-compose.dev.yml',True,dev_values)
assert d['services']['testing-engine']['environment']['CODE_JUDGE_URL']=='http://isolated-judge.test:8106'
assert all('/var/run/docker.sock' not in str(v.get('volumes', [])) for v in d['services'].values())
missing_judge=subprocess.run(['docker','compose','--env-file','/dev/null','-f','infra/docker-compose.dev.yml','--profile','dev','config','--quiet'],env={'PATH':os.environ['PATH'],'HOME':os.environ['HOME']},cwd=r,capture_output=True)
assert missing_judge.returncode!=0
# Render with the actual scalar smoke-job environment, not a second hand-built
# fixture: this catches newly required Compose variables omitted by CI.
workflow=(r/'.github/workflows/ci.yml').read_text()
smoke_block=workflow.split('\n  smoke:\n',1)[1]
smoke_env_block=smoke_block.split('\n    env:\n',1)[1].split('\n    steps:\n',1)[0]
smoke_env={'PATH':os.environ['PATH'],'HOME':os.environ['HOME']}
for line in smoke_env_block.splitlines():
 match=re.fullmatch(r'      ([A-Z][A-Z0-9_]*): (.+)',line)
 if match:
  value=match[2].strip()
  if value[:1] in ('"',"'") and value[-1:]==value[:1]:value=value[1:-1]
  assert '${{' not in value,'Smoke config regression requires literal synthetic env values'
  smoke_env[match[1]]=value
assert smoke_env.get('CODE_JUDGE_URL')=='http://127.0.0.1:1','Generic smoke must explicitly use its unreachable local judge endpoint'
assert smoke_env.get('CODE_JUDGE_TOKEN')=='synthetic-ci-unavailable-judge-token'
ci=cfg('infra/docker-compose.dev.yml',True,smoke_env)
ci_testing=ci['services']['testing-engine']['environment']
assert ci_testing['CODE_JUDGE_URL']==smoke_env['CODE_JUDGE_URL']
assert ci_testing['CODE_JUDGE_TOKEN']==smoke_env['CODE_JUDGE_TOKEN']
assert ci_testing['TESTING_ENGINE_TOKEN']==smoke_env['TESTING_ENGINE_TOKEN']
assert 'Verify unavailable code judge stays fail-closed' in smoke_block
assert 'Code judge isolation (disposable runner)' in workflow
assert 'tests/test_docker_acceptance.py' in workflow
assert d['services']['integration-engine']['environment']['APP_ENV']=='development'
assert d['name']=='comit-dev';assert d['services']['api']['environment']['DEV_AUTH_EXPOSE_CODE']=='true'
assert d['services']['api']['environment']['NODE_ENV']=='development'
assert all(v['profiles']==(['prod'] if n=='auth-mail-worker' else ['dev']) for n,v in d['services'].items())
fail=subprocess.run(['docker','compose','--env-file','/dev/null','-f','infra/docker-compose.yml','config','--quiet'],env={'PATH':os.environ['PATH'],'HOME':os.environ['HOME']},cwd=r,capture_output=True)
assert fail.returncode!=0
print('PASS: production/dev/actual CI smoke config, unavailable judge boundary, migration order, workers, isolation, required secrets, prod compatibility')
