"""Fail-closed deployment configuration regression tests (no Docker or secrets)."""
import copy
import importlib.util
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).parents[1] / 'infra/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class ProductionConfigTests(unittest.TestCase):
    """Validate security and persistent-data identities before live transitions."""

    def setUp(self):
        self.url = 'https://comit.droidje.com'
        self.config = {
            'services': {
                'api': {'environment': {'PUBLIC_SITE_URL': self.url, 'AUTH_ALLOWED_ORIGINS': self.url, 'AUTH_SECRET': 'x' * 32, 'AUTH_COOKIE_SECURE': 'true', 'NODE_ENV': 'production'}},
                'postgres': {}, 'qgramm-chat': {}},
            'volumes': {name: {'name': identity} for name, identity in [('pgdata', 'comit_pgdata'), ('chat-data', 'comit_chat-data'), ('harrier_cache', 'comit_harrier_cache')]}}

    def test_production_accepted(self):
        deploy.validate(self.config, self.url)

    def test_wrong_volume_rejected(self):
        self.config['volumes']['pgdata']['name'] = 'new_empty_volume'
        with self.assertRaises(ValueError):
            deploy.validate(self.config, self.url)

    def test_dev_flags_rejected_in_every_service(self):
        self.config['services']['postgres']['environment'] = {'FSP_DEMO_ENABLED': 'true'}
        with self.assertRaises(ValueError):
            deploy.validate(self.config, self.url)

    def test_insecure_cookie_rejected(self):
        self.config['services']['api']['environment']['AUTH_COOKIE_SECURE'] = 'false'
        with self.assertRaises(ValueError):
            deploy.validate(self.config, self.url)

    def test_localhost_and_wrong_origin_rejected(self):
        for origin in ['http://localhost:8088', 'https://localhost', 'https://other.example', self.url + ',https://localhost', self.url + ',https://example.com?x=1']:
            config = copy.deepcopy(self.config)
            config['services']['api']['environment']['AUTH_ALLOWED_ORIGINS'] = origin
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                deploy.validate(config, self.url)

    def test_public_url_must_be_https_root(self):
        for url in ['http://comit.droidje.com', self.url + '/path', 'https://user:pass@comit.droidje.com']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                deploy.validate(self.config, url)


class LegacyAdoptionTests(unittest.TestCase):
    """The explicitly authorized legacy upgrade must preserve data and recover old apps."""

    def setUp(self):
        self.live = [
            {'Config': {'Labels': {'com.docker.compose.service': name}},
             'Image': 'sha256:' + name,
             'Mounts': [{'Name': 'comit_pgdata', 'Destination': '/var/lib/postgresql/data'}] if name == 'postgres' else []}
            for name in sorted(deploy.LEGACY_SERVICES)]
        self.config = {'services': {name: {'build': {}} for name in deploy.LEGACY_SERVICES - {'postgres'}}}
        self.config['services'].update({'postgres': {}, 'qgramm-chat': {'build': {}}, 'agents-worker': {'build': {}}, 'auth-migrate': {'build': {}}})

    def test_explicit_legacy_plan_preserves_old_apps(self):
        images, apps, introduced = deploy.snapshot_plan(self.config, self.live, True)
        self.assertEqual(set(apps), deploy.LEGACY_SERVICES - {'postgres'})
        self.assertEqual(images['postgres'], 'sha256:postgres')
        self.assertEqual(set(introduced), {'qgramm-chat', 'agents-worker', 'auth-migrate'})

    def test_automatic_legacy_refusal_does_not_reserve_release(self):
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replies = [(sha + "\n").encode(), (sha + " refs/heads/main\n").encode(),
                       b"legacy-container\n", json.dumps(self.live).encode()]
            with patch.object(deploy, "ROOT", root), patch.object(deploy, "run", side_effect=replies) as runner:
                with self.assertRaisesRegex(ValueError, "explicit manual adoption"):
                    deploy.deploy(sha, root)
            self.assertFalse((root / "releases").exists())
            self.assertFalse(any("archive" in call.args[0] for call in runner.call_args_list))

    def test_default_deployment_refuses_incomplete_legacy_snapshot(self):
        with self.assertRaises(ValueError):
            deploy.snapshot_plan(self.config, self.live)

    def test_adoption_rejects_extra_or_missing_live_services(self):
        for live in [self.live[:-1], self.live + [{'Config': {'Labels': {'com.docker.compose.service': 'unexpected'}}, 'Image': 'sha256:extra', 'Mounts': []}]]:
            with self.subTest(count=len(live)), self.assertRaises(ValueError):
                deploy.snapshot_plan(self.config, live, True)

    def test_adoption_rejects_other_postgres_volume(self):
        next(c for c in self.live if c['Config']['Labels']['com.docker.compose.service'] == 'postgres')['Mounts'][0]['Name'] = 'new-empty-volume'
        with self.assertRaises(ValueError):
            deploy.snapshot_plan(self.config, self.live, True)

    def test_failed_upgrade_stops_introduced_services_and_recovers_old_apps(self):
        _, apps, introduced = deploy.snapshot_plan(self.config, self.live, True)
        with patch.object(deploy, 'run') as run:
            deploy.recover(['new-compose'], ['old-compose'], Path('/private/rollback.json'), apps, introduced)
        stop, rollback = [call.args[0] for call in run.call_args_list]
        self.assertEqual(stop[:4], ['new-compose', 'stop', '--timeout', '30'])
        self.assertEqual(set(stop[4:]), set(introduced))
        self.assertIn('--no-deps', rollback)
        self.assertIn('--no-build', rollback)
        self.assertEqual(set(rollback[-len(apps):]), deploy.LEGACY_SERVICES - {'postgres'})
        self.assertNotIn('auth-migrate', rollback)
        self.assertNotIn('down', stop + rollback)

    def test_stop_failure_still_attempts_old_app_recovery(self):
        with patch.object(deploy, 'run', side_effect=[RuntimeError('stop failed'), None]) as run:
            with self.assertRaisesRegex(RuntimeError, 'operator attention'):
                deploy.recover(['new-compose'], ['old-compose'], Path('/private/rollback.json'), ['api'], ['agents-worker'])
        self.assertEqual(run.call_count, 2)


class ImmutableRollbackTests(unittest.TestCase):
    """Checkout must never replace source configuration used for rollback."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(deploy, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        legacy = self.root / 'legacy'
        legacy.mkdir()
        self.snapshot = legacy / 'compose.json'
        self.snapshot.write_text('{}')
        self.snapshot.chmod(0o600)
        self.config = {'name': 'comit', 'services': {name: {} for name in deploy.LEGACY_SERVICES}, 'volumes': {'pgdata': {'name': 'comit_pgdata'}}}
        self.config['services']['postgres']['volumes'] = [{'type': 'volume', 'source': 'pgdata', 'target': '/var/lib/postgresql/data'}]

    def output(self, args, **kwargs):
        return json.dumps(self.config).encode() if kwargs.get('capture') else None

    def test_legacy_ignores_mutable_live_labels(self):
        with patch.object(deploy, 'run', side_effect=self.output):
            command, files = deploy.previous_compose({'com.docker.compose.project.config_files': '/runner/new-compose.yml'}, True)
        self.assertEqual(files, [str(self.snapshot)])
        self.assertNotIn('/runner/new-compose.yml', command)

    def test_legacy_snapshot_must_remain_private(self):
        self.snapshot.chmod(0o644)
        with self.assertRaises(ValueError):
            deploy.previous_compose({}, True)

    def test_legacy_snapshot_rejects_wrong_pgdata(self):
        self.config['volumes']['pgdata']['name'] = 'new-empty-volume'
        with patch.object(deploy, 'run', side_effect=self.output), self.assertRaises(ValueError):
            deploy.previous_compose({}, True)

    def test_legacy_snapshot_rejects_wrong_service_set(self):
        self.config['services']['unexpected'] = {}
        with patch.object(deploy, 'run', side_effect=self.output), self.assertRaises(ValueError):
            deploy.previous_compose({}, True)

    def test_legacy_rejects_mutable_bind_source(self):
        mutable = self.root / 'runner-nginx.conf'
        mutable.write_text('server {}')
        self.config['services']['web']['volumes'] = [{'type': 'bind', 'source': str(mutable), 'target': '/etc/nginx/conf.d/default.conf'}]
        with patch.object(deploy, 'run', side_effect=self.output), self.assertRaises(ValueError):
            deploy.previous_compose({}, True)

    def test_legacy_accepts_preserved_bind_source(self):
        source = self.root / 'legacy' / 'nginx.conf'
        source.write_text('server {}')
        self.config['services']['web']['volumes'] = [{'type': 'bind', 'source': str(source), 'target': '/etc/nginx/conf.d/default.conf'}]
        with patch.object(deploy, 'run', side_effect=self.output):
            deploy.previous_compose({}, True)

    def test_normal_release_rejects_runner_workspace(self):
        source = self.root / 'runner-compose.yml'
        source.write_text('{}')
        with self.assertRaises(ValueError):
            deploy.previous_compose({'com.docker.compose.project.config_files': str(source), 'com.docker.compose.project.working_dir': str(self.root)})

    def test_normal_release_accepts_exact_sha_source(self):
        release = self.root / 'releases' / ('a' * 40)
        release.mkdir(parents=True)
        source = release / 'compose.json'
        source.write_text('{}')
        with patch.object(deploy, 'run'):
            command, files = deploy.previous_compose({'com.docker.compose.project.config_files': str(source), 'com.docker.compose.project.working_dir': str(release)})
        self.assertEqual(files, [str(source)])
        self.assertIn(str(release), command)


class RunningReleaseTests(unittest.TestCase):
    """Background workers must stay running even when Compose has no healthcheck."""

    def setUp(self):
        self.container = {'Config': {'Labels': {'com.docker.compose.service': 'agents-worker',
            'com.docker.compose.project': 'comit'}, 'Env': ['COMIT_RELEASE=revision']},
            'State': {'Status': 'running'}, 'RestartCount': 0, 'Image': 'sha256:worker'}

    def check(self, running):
        deploy.check_running_release(running, ['agents-worker'],
                                     {'agents-worker': {'id': 'sha256:worker'}}, 'revision')

    def test_stable_worker_accepted(self):
        self.check([self.container])

    def test_missing_worker_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not stable'):
            self.check([])

    def test_worker_restart_or_exit_rejected(self):
        for status, restarts in [('restarting', 1), ('exited', 0), ('running', 1)]:
            container = copy.deepcopy(self.container)
            container.update(State={'Status': status}, RestartCount=restarts)
            with self.subTest(status=status, restarts=restarts), self.assertRaisesRegex(ValueError, 'not stable'):
                self.check([container])

    def test_unhealthy_service_rejected(self):
        self.container['State']['Health'] = {'Status': 'unhealthy'}
        with self.assertRaisesRegex(ValueError, 'not healthy'):
            self.check([self.container])


if __name__ == '__main__':
    unittest.main()
