"""Verify the single-port development stack through an existing API container."""
import argparse
import json
import subprocess
from pathlib import Path

PROBES = [
    ('api', 'http://api:8080/healthz', 200, 'api-core'),
    ('api ready', 'http://api:8080/ready', 200, 'api-core'),
    ('matching ready', 'http://matching-engine:8101/ready', 200, '"status":"ok"'),
    ('agents ready', 'http://agents-service:8104/ready', 200, 'ready'),
    ('testing', 'http://testing-engine:8102/healthz', 200, 'testing-engine'),
    ('integration', 'http://integration-engine:8103/healthz', 200, 'integration-engine'),
    ('verification', 'http://verification-engine:8105/healthz', 200, 'verification-engine'),
    ('blueprints', 'http://testing-engine:8102/blueprints', 200, 'algo-hash-lookup'),
    ('fsp demo', 'http://integration-engine:8103/fsp/participants/fsp-001', 200, ''),
    ('fsp absence', 'http://integration-engine:8103/fsp/participants/fsp-none', 404, ''),
]
for path, marker in [('/', 'работа настигает сама'), ('/candidate/', 'кабинет кандидата'),
                     ('/employer/', 'кабинет работодателя'), ('/testing/', 'сервис тестирования'),
                     ('/support/', 'служба поддержки'), ('/help/', 'Справка Comit')]:
    PROBES.append((path, 'http://web'+path, 200, marker))
for path, status, marker in [('/api/healthz', 200, 'api-core'), ('/api/auth/candidate/me', 401, ''),
                           ('/api/auth/employer/me', 401, ''), ('/api/candidate/testing/preflight?specialization=sql', 401, ''),
                           ('/api/internal/strict-schema', 404, ''), ('/api/metrics', 404, ''),
                           ('/api/public/showcase/candidates', 200, '"items"'),
                           ('/api/public/showcase/vacancies', 200, '"items"')]:
    PROBES.append((path, 'http://web'+path, status, marker))


def main() -> None:
    """Run bounded read-only probes without exposing internal ports or credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='comit-dev')
    args = parser.parse_args()
    if args.project not in ('comit-dev', 'comit-prerelease') and not args.project.startswith('comit-local-'):
        parser.error('Expected an isolated development project')
    root = Path(__file__).resolve().parent.parent
    code = '''const probes=JSON.parse(process.argv[1]);let failed=0;
for(const [name,url,status,marker] of probes){
try{const r=await fetch(url,{redirect:'error',signal:AbortSignal.timeout(15000)});const b=await r.text();
if(r.status!==status||b.length>2097152||!b.includes(marker))throw Error('contract');console.log('PASS '+name);}
catch{failed++;console.log('FAIL '+name);}}
console.log(failed?'Local smoke failed: '+failed:'Local smoke: all green');process.exitCode=failed?1:0;'''
    result = subprocess.run(['docker','compose','--env-file','/dev/null','-p',args.project,
        '-f',str(root/'infra/docker-compose.dev.yml'),'-f',str(root/'infra/docker-compose.local.yml'),
        '--profile','dev','exec','-T','api','node','--input-type=module','-e',code,json.dumps(PROBES)])
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
