/** Measure every API source file; PG test files get separate disposable databases. */
import { readdirSync, mkdirSync, rmSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const unit = process.argv.includes('--unit');
const execute = process.argv.includes('--execute');
const out = path.join(root, 'coverage', unit ? 'unit/api' : 'backend/api');
const testDirectory = path.join(root, 'apps/api/tests');
const tests = readdirSync(testDirectory).filter(name => name.endsWith('.test.ts')).sort();
const env = { ...process.env };
// The external chat contour requires its own acceptance instance; never use a live one here.
delete env.CHAT_TEST_SERVICE_URL;
delete env.CHAT_TEST_SERVICE_TOKEN;
for (const key of Object.keys(env)) {
  if ((key.endsWith('TEST_DATABASE_URL') && key !== 'COVERAGE_TEST_DATABASE_URL') || key === 'LEARNING_ACCEPTANCE_DATABASE_URL') delete env[key];
}
let database;
if (!unit) {
  try {
    database = new URL(env.COVERAGE_TEST_DATABASE_URL ?? '');
  } catch {
    throw new Error('COVERAGE_TEST_DATABASE_URL must identify an explicitly disposable *_test database');
  }
  if (!['postgres:', 'postgresql:'].includes(database.protocol) || !database.pathname.endsWith('_test')) {
    throw new Error('COVERAGE_TEST_DATABASE_URL must identify an explicitly disposable *_test database');
  }
}

if (execute) {
  const require = createRequire(path.join(root, 'apps/api/package.json'));
  const { Pool } = require('pg');
  const admin = database ? new Pool({ connectionString: database.href }) : undefined;
  let failed = false;
  try {
    for (const filename of tests) {
      const fixtureEnv = { ...env, COMIT_DISPOSABLE_DATABASE_TESTS: '1' };
      const name = `hiring_test_${Date.now()}_${randomBytes(8).toString('hex')}`;
      try {
        if (admin) {
          await admin.query(`CREATE DATABASE "${name}"`);
          const url = new URL(database);
          url.pathname = `/${name}`;
          const source = readFileSync(path.join(testDirectory, filename), 'utf8');
          for (const match of source.matchAll(/process\.env\.([A-Z_]+TEST_DATABASE_URL)/g)) fixtureEnv[match[1]] = url.href;
          fixtureEnv.PIPELINE_TEST_DATABASE_URL = url.href;
          // This bridge reads the real durable agents tables rather than substituting a fake store.
          if (filename === 'organization-agent-tools.pg.test.ts') {
            const prepared = spawnSync(env.COVERAGE_SCHEMA_PYTHON ?? 'python3', ['-c',
              'import os; from agents_service.database import migrate; migrate(os.environ["PIPELINE_TEST_DATABASE_URL"])'],
              { cwd: root, env: fixtureEnv, stdio: 'inherit' });
            if (prepared.status !== 0) throw new Error('agents schema preparation failed');
          }
        }
        const result = spawnSync('pnpm', ['--filter', '@comit/api', 'exec', 'tsx', '--test',
          '--test-concurrency=1', '--test-reporter=spec', '--test-reporter-destination=stdout',
          '--test-reporter=junit', `--test-reporter-destination=${path.join(out, filename + '.xml')}`,
          `tests/${filename}`], { cwd: root, env: fixtureEnv, stdio: 'inherit' });
        if (result.error) throw result.error;
        if (result.status !== 0) failed = true;
      } finally {
        if (admin) await admin.query(`DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`);
      }
    }
  } finally {
    await admin?.end();
  }
  process.exit(failed ? 1 : 0);
}

// API imports use the same compiled shared package as the production Node runtime.
const shared = spawnSync('pnpm', ['--filter', '@comit/shared', 'build'],
  { cwd: root, env, stdio: 'inherit' });
if (shared.error) throw shared.error;
if (shared.status !== 0) process.exit(shared.status ?? 1);

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
const measured = spawnSync('pnpm', ['exec', 'c8', '--all', '--include=apps/api/src/**/*.ts',
  '--reporter=json-summary', '--reporter=lcov', '--reporter=text', `--reports-dir=${out}`,
  'node', fileURLToPath(import.meta.url), '--execute', ...(unit ? ['--unit'] : [])],
  { cwd: root, env, stdio: 'inherit' });
if (measured.error) throw measured.error;
if (measured.status !== 0) process.exit(measured.status ?? 1);
if (!unit) {
  const junit = tests.flatMap(filename => ['--junit', path.join(out, filename + '.xml')]);
  const result = spawnSync(process.env.PYTHON ?? 'python3', [path.join(root, 'tests/coverage-gates/check.py'),
    'api', path.join(out, 'coverage-summary.json'), ...junit, '--allow-skip',
    'invitation chat: two authenticated participants, files, polling, retries, consent and closure'],
    { cwd: root, stdio: 'inherit' });
  process.exit(result.status ?? 1);
}
