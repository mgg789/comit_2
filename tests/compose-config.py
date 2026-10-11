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
assert missing_judge.returncode==0
assert d['services']['testing-engine']['environment']['TESTING_ENABLED']=='true'
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

# Legal Markdown must exist in the Docker build context before Vite embeds it.
dockerignore = (r / '.dockerignore').read_text()
assert '\n!docs/legal/\n!docs/legal/**\n' in dockerignore
assert '\ndocs\n' not in dockerignore
web_dockerfile = (r / 'infra/web.Dockerfile').read_text()
assert web_dockerfile.index('COPY docs/legal ./docs/legal') < web_dockerfile.index('RUN pnpm --filter')
local = subprocess.run([str(r / 'infra/local-dev.sh'), 'config', '--format', 'json'], env=dev_values, cwd=r, capture_output=True, text=True)
assert local.returncode == 0, local.stderr
local_cfg = json.loads(local.stdout)
assert local_cfg['services']['testing-engine']['environment']['TESTING_ENABLED'] == 'false'
assert local_cfg['name'] == 'comit-local'
for name, service in local_cfg['services'].items():
    ports = service.get('ports', [])
    if name == 'web':
        assert len(ports) == 1 and ports[0]['host_ip'] == '127.0.0.1'
    else:
        assert not ports, name
judge_values = dict(dev_values)
judge = subprocess.run([str(r / 'infra/local-dev.sh'), '--with-judge', 'config', '--format', 'json'], env=judge_values, cwd=r, capture_output=True, text=True)
assert judge.returncode == 0, judge.stderr
assert json.loads(judge.stdout)['services']['testing-engine']['environment']['TESTING_ENABLED'] == 'true'
print('PASS: isolated single-port launcher, explicit real judge mode, bundled legal sources')

for service in (s['api'], d['services']['api']):
    assert service['environment']['YANDEX_OAUTH_ENABLED'] == 'false'
    assert service['environment']['YANDEX_OAUTH_CLIENT_SECRET'] == ''
print('PASS: OAuth compose defaults remain disabled')

init = local_cfg['services']['model-cache-init']
assert init['environment']['HF_HOME'] == '/models'
assert any(v['source'] == 'harrier_cache' and v['target'] == '/models' for v in init['volumes'])
assert 'snapshot_download(MODEL_ID, revision=MODEL_REVISION)' in init['command'][-1]
assert 'pinned_harrier_cache_incomplete' in init['command'][-1]
assert local_cfg['services']['matching-engine']['depends_on']['model-cache-init']['condition'] == 'service_completed_successfully'
assert 'COMIT_LOCAL_PROJECT=comit-dev ./infra/local-dev.sh' in (r / 'Makefile').read_text()
print('PASS: pinned cache bootstrap gates matching readiness and fresh local configuration')

nginx_gateway = (r / 'infra/nginx/nginx.conf').read_text()
for role in ('candidate', 'employer'):
    block = nginx_gateway.split(f'location = /api/auth/{role}/yandex/callback {{', 1)[1].split('}', 1)[0]
    assert 'access_log off;' in block
    assert f'proxy_pass http://api:8080/auth/{role}/yandex/callback;' in block
    assert 'proxy_read_timeout 75s;' in block and 'proxy_set_header X-Forwarded-Proto $scheme;' in block
host_nginx = (r / 'infra/nginx/comit.droidje.com.conf').read_text()
host_blocks = host_nginx.split('location ~ ^/api/auth/(candidate|employer)/yandex/callback$ {')[1:]
assert len(host_blocks) == 2
assert all('access_log off;' in block.split('}', 1)[0] for block in host_blocks)
assert 'proxy_read_timeout 90s;' in host_blocks[1].split('}', 1)[0]
print('PASS: OAuth callback code/state excluded at both nginx layers')

assert p['services']['web']['ports'][0]['host_ip'] == '127.0.0.1'
assert d['services']['web']['ports'][0].get('host_ip', '0.0.0.0') == '0.0.0.0'
assert 'location = /api/metrics { return 404; }' in nginx_gateway
for header in ('X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy', 'Content-Security-Policy'):
    assert f'add_header {header}' in nginx_gateway and f'add_header {header}' in host_nginx
assert 'add_header Strict-Transport-Security "max-age=15552000" always;' in host_nginx
assert "style-src 'self' 'unsafe-inline'" in nginx_gateway and "script-src 'self' 'wasm-unsafe-eval'" in nginx_gateway
assert "media-src 'self' blob:" in nginx_gateway
print('PASS: compatible web security headers, private metrics, loopback production gateway')


# Provider changes must reach every runtime sharing the completion clients.
provider_services = ['testing-engine', 'agents-service', 'agents-worker', 'organization-agent-worker']
for name in provider_services:
    production_env = s[name]['environment']
    assert production_env['LLM_PROVIDER'] == 'timeweb', name
    assert production_env['TIMEWEB_MODEL'] == 'timeweb/gpt-oss-120b', name
    assert production_env['TIMEWEB_REASONING_EFFORT'] == 'low', name
    assert production_env['JEV_PROVIDER'] == 'systemone', name
    assert d['services'][name]['environment']['LLM_PROVIDER'] == 'deepseek', name
for name in ['agents-service', 'agents-worker', 'organization-agent-worker']:
    assert s[name]['environment']['AGENT_INPUT_USD_PER_MILLION'] == '', 'No legacy tariff for Timeweb'
    assert s[name]['environment']['AGENT_OUTPUT_USD_PER_MILLION'] == '', 'No legacy tariff for Timeweb'
print('PASS: provider selection reaches all workers; Timeweb never inherits a legacy tariff')
