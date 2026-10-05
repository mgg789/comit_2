import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Pool } from "pg";
import type { PoolClient } from "pg";

/** A stable lock namespace shared by every API migration process. */
export const AUTH_MIGRATION_LOCK = 2188463147n;
const migrationDirectory = join(dirname(fileURLToPath(import.meta.url)), "../../migrations");

interface MigrationRow {
  checksum: string;
}

/** Apply checked-in auth migrations exactly once; serving never invokes this function. */
export async function migrate(pool: Pool): Promise<string[]> {
  const client = await pool.connect();
  const applied: string[] = [];
  try {
    await client.query("BEGIN");
    await client.query("SELECT pg_advisory_xact_lock($1)", [AUTH_MIGRATION_LOCK]);
    await client.query(`
      CREATE TABLE IF NOT EXISTS auth_migrations (
        name text PRIMARY KEY,
        checksum text NOT NULL,
        applied_at timestamptz NOT NULL DEFAULT now()
      )
    `);

    const migration = await readFile(join(migrationDirectory, "001_accounts.sql"), "utf8");
    const name = basename("001_accounts.sql");
    const checksum = createHash("sha256").update(migration, "utf8").digest("hex");
    const existing = await client.query<MigrationRow>("SELECT checksum FROM auth_migrations WHERE name = $1", [name]);
    const row = existing.rows[0];
    if (row && row.checksum !== checksum) throw new Error(`applied migration changed: ${name}`);
    if (!row) {
      await client.query(migration);
      await client.query("INSERT INTO auth_migrations(name, checksum) VALUES ($1, $2)", [name, checksum]);
      applied.push(name);
    }
    await client.query("COMMIT");
    return applied;
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // Preserve the migration error when the connection itself is unavailable.
    }
    throw error;
  } finally {
    client.release();
  }
}

/** Explicit CLI entry point used by deployment jobs; the API server does not call it. */
export async function runMigrationCli(): Promise<void> {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error("DATABASE_URL is required for auth migration");
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    for (const name of await migrate(pool)) process.stdout.write(`${name}\n`);
  } finally {
    await pool.end();
  }
}

/** Keep the imported PoolClient type visible to generated declaration checks. */
export type AuthMigrationClient = PoolClient;

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  await runMigrationCli();
}
