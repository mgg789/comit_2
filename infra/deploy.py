#!/usr/bin/env python3
"""Deploy an exact Git revision with private config, backup and app-only rollback."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

ROOT = Path('/home/mgg/comit')


def run(args, *, capture=False, **kwargs):
    """Run without leaking command output containing resolved secrets."""
    result = subprocess.run(args, stdout=kwargs.pop('stdout', subprocess.PIPE if capture else None),
                            stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise RuntimeError(f'Command failed: {args[0]} (exit {result.returncode}); private output suppressed')
    return result.stdout


def validate(config, public_url):
    """Reject development configuration and wrong persistent volume identities."""
    parsed = urlparse(public_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.path not in ('', '/'):
        raise ValueError('PUBLIC_SITE_URL must be a root HTTPS URL')
    services = config['services']
    api = services['api']['environment']
    if api.get('AUTH_COOKIE_SECURE') != 'true' or api.get('NODE_ENV') != 'production':
        raise ValueError('Production cookies and NODE_ENV required')
    origins = str(api.get('AUTH_ALLOWED_ORIGINS', '')).split(',')
    if public_url.rstrip('/') not in [origin.strip().rstrip('/') for origin in origins]:
        raise ValueError('Public HTTPS origin must be explicitly allowed')
    for origin in origins:
        u = urlparse(origin.strip())
        if u.scheme != 'https' or not u.hostname or u.hostname in ('localhost', '127.0.0.1') or u.path not in ('', '/') or u.username or u.query or u.fragment:
            raise ValueError('Only explicit production HTTPS origins allowed')
    if len(str(api.get('AUTH_SECRET', '')).encode()) < 32:
        raise ValueError('AUTH_SECRET must contain at least 32 bytes')
    for service in services.values():
        env = service.get('environment', {})
        for flag in ('DEV_AUTH_EXPOSE_CODE', 'FSP_DEMO_ENABLED'):
            if str(env.get(flag, 'false')).lower() not in ('false', '0'):
                raise ValueError('Development flags forbidden')
    for volume, identity in [('pgdata', 'comit_pgdata'), ('chat-data', 'comit_chat-data'), ('harrier_cache', 'comit_harrier_cache')]:
        if config['volumes'][volume]['name'] != identity:
            raise ValueError('Persistent volume identity changed')
    for name in ('postgres', 'qgramm-chat'):
        if name not in services:
            raise ValueError('Required storage service missing')


LEGACY_SERVICES = frozenset({'web', 'api', 'matching-engine', 'postgres', 'testing-engine', 'integration-engine'})


def snapshot_plan(config, live, adopt_legacy=False):
    """Validate live storage and return only previously running apps eligible for rollback.

    Legacy adoption requires exactly the known six-service contour. New volumes must
    still be provisioned by the operator; only the legacy PostgreSQL mount is reused.
    """
    images = {c['Config']['Labels']['com.docker.compose.service']: c['Image'] for c in live}
    if len(images) != len(live):
        raise ValueError('Ambiguous scaled live snapshot; no transition')
    app_services = {name for name, service in config['services'].items() if 'build' in service and not name.endswith('-migrate')}
    mounts = [('postgres', 'comit_pgdata', '/var/lib/postgresql/data')]
    if adopt_legacy:
        if set(images) != LEGACY_SERVICES:
            raise ValueError('Legacy adoption requires the exact six-service allowlist')
        rollback_services = sorted(LEGACY_SERVICES - {'postgres'})
        if not set(rollback_services).issubset(app_services):
            raise ValueError('Legacy apps missing from target; no transition')
    else:
        if not app_services.issubset(images):
            raise ValueError('Previous compatible app snapshot incomplete; explicit legacy adoption required')
        rollback_services = sorted(app_services)
        mounts += [('qgramm-chat', 'comit_chat-data', '/data'), ('matching-engine', 'comit_harrier_cache', '/models')]
    for service, identity, destination in mounts:
        container = next((c for c in live if c['Config']['Labels']['com.docker.compose.service'] == service), None)
        if container is None or not any(m.get('Name') == identity and m.get('Destination') == destination for m in container.get('Mounts', [])):
            raise ValueError('Live persistent-volume identity mismatch; no transition')
    new_services = sorted(set(config['services']) - set(images))
    return images, rollback_services, new_services


def previous_compose(labels, adopt_legacy=False):
    """Resolve rollback sources only from a private legacy snapshot or immutable releases.

    Runner workspaces are mutable during checkout and must never be rollback inputs.
    Resolved configuration is captured privately and never printed or persisted again.
    """
    if adopt_legacy:
        legacy = ROOT / 'legacy' / 'compose.json'
        if not legacy.is_file() or legacy.is_symlink() or legacy.stat().st_mode & 0o077:
            raise ValueError('Private immutable legacy Compose snapshot required')
        prior_files = [str(legacy)]
        prior_dir = legacy.parent
    else:
        prior_files = labels.get('com.docker.compose.project.config_files', '').split(',')
        releases = (ROOT / 'releases').resolve()
        for file in prior_files:
            path = Path(file)
            if not path.is_absolute() or not path.is_file():
                raise ValueError('Previous immutable release source unavailable')
            try:
                relative = path.resolve().relative_to(releases)
            except ValueError:
                raise ValueError('Mutable workspace forbidden as rollback source') from None
            if not relative.parts or not re.fullmatch(r'[0-9a-f]{40}', relative.parts[0]):
                raise ValueError('Previous release source must belong to an exact SHA')
        prior_dir = Path(labels.get('com.docker.compose.project.working_dir', ''))
        try:
            prior_dir.resolve().relative_to(releases)
        except ValueError:
            raise ValueError('Mutable working directory forbidden as rollback source') from None
    prior_base = ['docker', 'compose', '-p', 'comit', '--env-file', str(ROOT / '.env'), '--project-directory', str(prior_dir)]
    for file in prior_files:
        prior_base += ['-f', file]
    run(prior_base + ['config', '--quiet'])
    if adopt_legacy:
        config = json.loads(run(prior_base + ['config', '--format', 'json'], capture=True))
        if set(config['services']) != LEGACY_SERVICES or config.get('name') != 'comit':
            raise ValueError('Legacy Compose snapshot must describe the exact six-service project')
        mounts = config['services']['postgres'].get('volumes', [])
        if not any(m.get('type') == 'volume' and m.get('target') == '/var/lib/postgresql/data' and config.get('volumes', {}).get(m.get('source'), {}).get('name') == 'comit_pgdata' for m in mounts):
            raise ValueError('Legacy snapshot PostgreSQL volume identity mismatch')
        # Source config files must remain stable even if checkout replaces the runner tree.
        for service in config['services'].values():
            for mount in service.get('volumes', []):
                if mount.get('type') == 'bind':
                    path = Path(mount.get('source', ''))
                    try:
                        path.resolve().relative_to((ROOT / 'legacy').resolve())
                    except ValueError:
                        raise ValueError('Legacy bind sources must be copied into the immutable legacy directory') from None
                    if not path.exists():
                        raise ValueError('Legacy immutable bind source unavailable')
    return prior_base, prior_files


def recover(command, prior_base, rollback_file, rollback_services, new_services):
    """Stop introduced services and recover old apps without rerunning migrations or removing data."""
    failures = []
    if new_services:
        try:
            run(command + ['stop', '--timeout', '30', *new_services])
        except RuntimeError:
            failures.append('new service stop failed')
    try:
        run(prior_base + ['-f', str(rollback_file), 'up', '-d', '--no-build', '--no-deps', *rollback_services])
    except RuntimeError:
        failures.append('previous app recovery failed')
    if failures:
        raise RuntimeError('Rollback needs operator attention: ' + '; '.join(failures))


def private_json(path, value):
    """Write manifests without environment or credential values."""
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.chmod(0o600)


def main():
    """Serialize deployment, build before migration, and recover compatible app images."""
    parser = argparse.ArgumentParser()
    parser.add_argument('sha')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--adopt-legacy', action='store_true', help='Explicitly adopt the exact legacy six-service stack')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.sha):
        raise ValueError('Expected full Git SHA')
    if not ROOT.is_dir() or not (ROOT / '.env').is_file():
        raise ValueError('Provision private server environment first')
    os.umask(0o077)
    with (ROOT / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        deploy(args.sha, Path(args.repo), args.adopt_legacy)


def check_running_release(running, app_services, built_images, sha):
    """Reject missing, unhealthy or restarting apps, including workers without healthchecks."""
    actual = {c['Config']['Labels']['com.docker.compose.service']: c for c in running}
    for name in app_services:
        container = actual.get(name)
        if not container or container['State'].get('Status') != 'running' or container.get('RestartCount', 0):
            raise ValueError(f'Release service is not stable: {name}')
        if container['State'].get('Health', {}).get('Status', 'healthy') != 'healthy':
            raise ValueError(f'Release service is not healthy: {name}')
        if container['Image'] != built_images[name]['id'] or container['Config']['Labels'].get('com.docker.compose.project') != 'comit':
            raise ValueError('Running release image identity mismatch')
        if f'COMIT_RELEASE={sha}' not in container['Config'].get('Env', []):
            raise ValueError('Running release stamp mismatch')


def deploy(sha, repo, adopt_legacy=False):
    """Execute a release; database restore and migration downgrade are never automatic."""
    resolved = run(['git', '-C', str(repo), 'rev-parse', f'{sha}^{{commit}}'], capture=True).decode().strip()
    if resolved != sha:
        raise ValueError('Checkout revision mismatch')
    # Do not deploy a stale CI result after main advanced.
    remote = run(['git', '-C', str(repo), 'ls-remote', 'origin', 'refs/heads/main'], capture=True).decode().split()[0]
    if remote != sha:
        raise ValueError('CI revision is no longer current main')
    # A first automatic main deploy must refuse legacy adoption before reserving
    # the SHA directory, so an approved manual adoption can use the same revision.
    existing_ids = run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=comit'], capture=True).decode().split()
    if existing_ids and not adopt_legacy:
        existing = json.loads(run(['docker', 'inspect', *existing_ids], capture=True))
        names = {item['Config']['Labels']['com.docker.compose.service'] for item in existing}
        if names == LEGACY_SERVICES:
            raise ValueError('Legacy stack requires explicit manual adoption; no release reserved')
    releases = ROOT / 'releases'
    releases.mkdir(exist_ok=True)
    release = releases / sha
    if release.exists():
        raise ValueError('Release already exists; inspect prior attempt before retrying')
    release.mkdir()
    archive = run(['git', '-C', str(repo), 'archive', sha], capture=True)
    run(['tar', '-x', '-C', str(release)], input=archive)
    base = ['docker', 'compose', '-p', 'comit', '--env-file', str(ROOT / '.env'), '-f', str(release / 'infra/docker-compose.prod.yml')]
    run(base + ['config', '--quiet'])
    config = json.loads(run(base + ['config', '--format', 'json'], capture=True))
    url = config['services']['api']['environment']['PUBLIC_SITE_URL']
    validate(config, url)
    for identity in ('comit_pgdata', 'comit_chat-data', 'comit_harrier_cache'):
        run(['docker', 'volume', 'inspect', identity], capture=True)
    app_services = [name for name, service in config['services'].items() if 'build' in service and not name.endswith('-migrate')]
    ids = run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=comit'], capture=True).decode().split()
    if not ids:
        raise ValueError('Existing production required; bootstrap must be provisioned separately')
    live = json.loads(run(['docker', 'inspect', *ids], capture=True))
    images, rollback_services, new_services = snapshot_plan(config, live, adopt_legacy)
    api = next(c for c in live if c['Config']['Labels']['com.docker.compose.service'] == 'api')
    prior_base, prior_files = previous_compose(api['Config']['Labels'], adopt_legacy)
    rollback_file = release / 'rollback-images.json'
    private_json(rollback_file, {'services': {name: {'image': images[name]} for name in rollback_services}})
    override = release / 'release-images.json'
    release_services = {name: {'image': f'comit-release-{name}:{sha}', 'build': {'labels': {'org.opencontainers.image.revision': sha}}, 'environment': {'COMIT_RELEASE': sha}} for name, service in config['services'].items() if 'build' in service}
    # Preserve the exact running database image; CD never upgrades PostgreSQL implicitly.
    release_services['postgres'] = {'image': images['postgres']}
    private_json(override, {'services': release_services})
    command = base + ['-f', str(override)]
    run(command + ['build'])
    built_images = {}
    for name, service in config['services'].items():
        if 'build' in service:
            image = json.loads(run(['docker', 'image', 'inspect', f'comit-release-{name}:{sha}'], capture=True))[0]
            built_images[name] = {'id': image['Id'], 'digests': image.get('RepoDigests', [])}
            if image['Config']['Labels'].get('org.opencontainers.image.revision') != sha:
                raise ValueError('Built image revision mismatch')
    backups = ROOT / 'backups'
    backups.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f'{sha}-', suffix='.dump', dir=backups, delete=False) as backup:
        run(['docker', 'exec', next(c['Id'] for c in live if c['Config']['Labels']['com.docker.compose.service'] == 'postgres'), 'pg_dump', '-U', 'comit', '-d', 'comit', '-Fc'], stdout=backup)
    # Verification uses the same PostgreSQL major image, never restoring to live data.
    run(['docker', 'run', '--rm', '-v', f'{backup.name}:/backup.dump:ro', '--entrypoint', 'pg_restore', 'pgvector/pgvector:pg16', '--list', '/backup.dump'], capture=True)
    private_json(release / 'manifest.json', {'sha': sha, 'adopt_legacy': adopt_legacy, 'introduced_services': new_services, 'built_images': built_images, 'previous_images': images, 'previous_compose_files': prior_files, 'backup': backup.name})
    try:
        run(command + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '240'])
        running_ids = run(command + ['ps', '-q'], capture=True).decode().split()
        running = json.loads(run(['docker', 'inspect', *running_ids], capture=True))
        check_running_release(running, app_services, built_images, sha)
        # Compose --wait cannot detect a worker crash after its process initially starts.
        time.sleep(20)
        running_ids = run(command + ['ps', '-q'], capture=True).decode().split()
        running = json.loads(run(['docker', 'inspect', *running_ids], capture=True))
        check_running_release(running, app_services, built_images, sha)
        smoke_env = os.environ.copy()
        for key, value in config['services']['api']['environment'].items():
            if key == 'PUBLIC_SITE_URL':
                smoke_env[key] = str(value)
        ports = {'web': 'WEB_PORT', 'api': 'API_PORT', 'matching-engine': 'MATCHING_PORT', 'testing-engine': 'TESTING_PORT', 'integration-engine': 'INTEGRATION_PORT', 'agents-service': 'AGENTS_PORT', 'verification-engine': 'VERIFICATION_PORT'}
        for service, key in ports.items():
            smoke_env[key] = str(config['services'][service]['ports'][0]['published'])
        smoke_env['SMOKE_MODE'] = 'production'
        run(['bash', str(release / 'tests/smoke.sh')], env=smoke_env)
        for path, expected in [('/', '200'), ('/candidate/', '200'), ('/employer/', '200'), ('/testing/', '404'), ('/api/auth/candidate/me', '401'), ('/api/auth/employer/me', '401'), ('/api/public/showcase/candidates', '200'), ('/api/public/showcase/vacancies', '200'), ('/api/ready', '200')]:
            status = run(['curl', '-sS', '--max-time', '15', '-o', '/dev/null', '-w', '%{http_code}', url.rstrip('/') + path], capture=True).decode()
            if status != expected:
                raise ValueError('Public HTTPS smoke failed')
    except Exception:
        print('Deployment failed; recovering previous app images only. Database is retained.', file=sys.stderr)
        recover(command, prior_base, rollback_file, rollback_services, new_services)
        raise
    current = ROOT / 'current'
    temp_link = ROOT / 'current.next'
    temp_link.unlink(missing_ok=True)
    temp_link.symlink_to(release)
    temp_link.replace(current)
    print(f'Deployed and verified release {sha}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
