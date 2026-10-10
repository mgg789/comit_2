/** Employer company identity stored independently of invitation history. */
import type { Pool, QueryResultRow } from "pg";
import type { EmployerProfileMutation, EmployerProfileView } from "@comit/shared";

/** Safe public error codes for profile validation and revision conflicts. */
export class EmployerProfileError extends Error {
  constructor(public readonly status: number, public readonly code: string) { super(code); }
}
interface ProfileRow extends QueryResultRow { company_name: string; revision: string; updated_at: Date }
const view = (row?: ProfileRow): EmployerProfileView => row ? {
  company_name: row.company_name, revision: Number(row.revision), updated_at: row.updated_at.toISOString(),
} : { company_name: null, revision: 0, updated_at: null };

/** Store validated account-owned company names; writes lock the account for initial CAS. */
export class EmployerProfileStore {
  constructor(private readonly pool: Pool) {}
  /** Read the account identity; an unconfigured employer has revision zero. */
  async get(ownerId: string): Promise<EmployerProfileView> {
    const account = await this.pool.query("SELECT id FROM accounts WHERE id=$1 AND role='employer'", [ownerId]);
    if (!account.rowCount) throw new EmployerProfileError(404, "employer_not_found");
    return view((await this.pool.query<ProfileRow>("SELECT company_name,revision,updated_at FROM employer_profiles WHERE owner_id=$1", [ownerId])).rows[0]);
  }
  /** Trim the name and atomically replace only the caller's expected revision. */
  async update(ownerId: string, input: EmployerProfileMutation): Promise<EmployerProfileView> {
    const name = typeof input.company_name === "string" ? input.company_name.trim() : "";
    if (!name || [...name].length > 200 || !Number.isSafeInteger(input.expected_revision) || input.expected_revision < 0)
      throw new EmployerProfileError(422, "invalid_employer_profile");
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const account = await client.query("SELECT id FROM accounts WHERE id=$1 AND role='employer' FOR UPDATE", [ownerId]);
      if (!account.rowCount) throw new EmployerProfileError(404, "employer_not_found");
      const current = (await client.query<ProfileRow>("SELECT company_name,revision,updated_at FROM employer_profiles WHERE owner_id=$1", [ownerId])).rows[0];
      if (Number(current?.revision ?? 0) !== input.expected_revision) throw new EmployerProfileError(409, "employer_profile_revision_conflict");
      if (input.expected_revision === Number.MAX_SAFE_INTEGER) throw new EmployerProfileError(409, "employer_profile_revision_exhausted");
      const result = await client.query<ProfileRow>(`INSERT INTO employer_profiles(owner_id,company_name) VALUES($1,$2)
        ON CONFLICT(owner_id) DO UPDATE SET company_name=EXCLUDED.company_name,revision=employer_profiles.revision+1,updated_at=clock_timestamp()
        RETURNING company_name,revision,updated_at`, [ownerId, name]);
      await client.query("COMMIT");
      return view(result.rows[0]);
    } catch (error) { await client.query("ROLLBACK"); throw error; }
    finally { client.release(); }
  }
}
