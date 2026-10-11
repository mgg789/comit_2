#!/usr/bin/env python3
"""Transfer explicitly selected demo projections without replacing target rows.

Uses Docker's existing PostgreSQL authentication; never reads .env. Source data
is kept in memory and SQL is passed through stdin. Target table locks and one
transaction make collision checks and insertion atomic. Dry run rolls back.
"""
import argparse
import json
import re
import shlex
import subprocess
from datetime import datetime, timezone

TABLES = {
    "public.accounts": "id",
    "public.candidate_profiles": "candidate_id",
    "public.employer_profiles": "owner_id",
    "candidate_pipeline.strict_schemas": "version",
    "candidate_pipeline.candidates": "candidate_id",
    "candidate_pipeline.pool": "candidate_id",
    "vacancy_pipeline.vacancies": "vacancy_id",
    "vacancy_pipeline.ready": "vacancy_id",
    "agents_service.drafts": "draft_id",
    "public.candidate_profile_outbox": "id",
}
OWNER_FILTER = "id IN (SELECT candidate_id FROM public.candidate_profiles UNION SELECT owner_id FROM public.employer_profiles UNION SELECT owner_id FROM vacancy_pipeline.vacancies)"
ACK_FILTER = "state='delivered' AND EXISTS (SELECT 1 FROM public.candidate_profiles p JOIN candidate_pipeline.pool c USING(candidate_id) WHERE p.candidate_id=candidate_profile_outbox.candidate_id AND p.revision=candidate_profile_outbox.source_revision AND c.revision=candidate_profile_outbox.projected_revision)"
TOMBSTONES = {
    "candidate_pipeline.candidates": [("candidate_pipeline.tombstones", "candidate_id")],
    "vacancy_pipeline.vacancies": [("vacancy_pipeline.tombstones", "vacancy_id"), ("agents_service.vacancy_tombstones", "vacancy_id")],
    "public.employer_profiles": [("vacancy_pipeline.employer_tombstones", "owner_id"), ("agents_service.employer_tombstones", "owner_id")],
}


def command(container, remote=None):
    """Build a fixed psql command, with an optional SSH transport."""
    argv = ["docker", "exec", "-i", container, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "comit", "-d", "comit", "-At"]
    if remote:
        return ["ssh", "-i", remote.identity, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-p", str(remote.port), remote.host, shlex.join(argv)]
    return argv


def run(argv, sql):
    """Execute SQL privately; suppress database errors that can contain rows."""
    result = subprocess.run(argv, input=sql, text=True, capture_output=True)
    if result.returncode:
        # PostgreSQL DETAIL may include emails or complete conflicting records.
        errors = [line for line in result.stderr.splitlines() if "ERROR:" in line]
        safe = next((line for line in errors if "demo_transfer" in line), "database command failed; sensitive diagnostics suppressed")
        raise RuntimeError(safe)
    return result.stdout


def export(source):
    """Take one consistent source snapshot and export only the allowlist."""
    metadata = run(source, "SELECT json_agg(json_build_object('table',table_schema||'.'||table_name,'column',column_name) ORDER BY table_schema,table_name,ordinal_position) FROM information_schema.columns WHERE table_schema IN ('public','candidate_pipeline','vacancy_pipeline','agents_service');")
    columns = {table: [] for table in TABLES}
    for row in json.loads(metadata):
        if row["table"] in columns:
            columns[row["table"]].append(row["column"])
    sql = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
    for i, table in enumerate(TABLES):
        names = ",".join('"' + c + '"' for c in columns[table])
        filters = {"public.accounts": OWNER_FILTER, "public.candidate_profile_outbox": ACK_FILTER}
        where = " WHERE " + filters[table] if table in filters else ""
        sql += f"\\echo START_{i}\nCOPY (SELECT {names} FROM {table}{where}) TO STDOUT;\n\\echo END_{i}\n"
    sql += "COMMIT;\n"
    output = run(source, sql)
    data = {}
    for i, table in enumerate(TABLES):
        found = re.search(rf"^START_{i}\n(.*?)^END_{i}\n", output, re.M | re.S)
        if not found:
            raise RuntimeError("source export marker missing")
        data[table] = found.group(1)
    return columns, data


def transfer_sql(columns, data, apply):
    """Generate atomic insert-only SQL; changed IDs and email collisions abort."""
    sql = "BEGIN; SET LOCAL lock_timeout='15s'; SET LOCAL statement_timeout='120s';\n"
    for table in TABLES:
        sql += f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE;\n"
    sql += "LOCK TABLE candidate_pipeline.settings IN SHARE MODE;\n"
    for table in sorted({table for entries in TOMBSTONES.values() for table, _ in entries}):
        sql += f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE;\n"
    for i, (table, key) in enumerate(TABLES.items()):
        names = ",".join('"' + c + '"' for c in columns[table])
        sql += f"CREATE TEMP TABLE demo_{i} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP;\nCOPY demo_{i} ({names}) FROM STDIN;\n{data[table]}\\.\n"
        if table in ("candidate_pipeline.pool", "vacancy_pipeline.ready"):
            sql += f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_{i} p WHERE NOT EXISTS (SELECT 1 FROM candidate_pipeline.strict_schemas sc JOIN candidate_pipeline.settings st ON st.active_schema_version=sc.version WHERE sc.version=p.schema_version AND sc.schema_hash=p.schema_hash)) THEN RAISE EXCEPTION 'demo_transfer inactive schema projection: {table}'; END IF; END $$;\n"
        for deleted_table, deleted_key in TOMBSTONES.get(table, []):
            sql += f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_{i} s JOIN {deleted_table} t ON s.{key}=t.{deleted_key}) THEN RAISE EXCEPTION 'demo_transfer tombstone collision: {table}'; END IF; END $$;\n"
        compare = columns[table]
        if table == "candidate_pipeline.strict_schemas":
            compare = [c for c in compare if c not in ("created_at", "updated_at")]
        left = ",".join('s."' + c + '"' for c in compare)
        right = ",".join('t."' + c + '"' for c in compare)
        sql += f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_{i} s JOIN {table} t USING ({key}) WHERE ROW({left}) IS DISTINCT FROM ROW({right})) THEN RAISE EXCEPTION 'demo_transfer differing ID collision: {table}'; END IF; END $$;\n"
        if table == "public.accounts":
            sql += "DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_0 s JOIN public.accounts t ON lower(s.email)=lower(t.email) WHERE s.id<>t.id) THEN RAISE EXCEPTION 'demo_transfer email collision'; END IF; END $$;\n"
        sql += f"SELECT '{table}',count(*),count(*) FILTER (WHERE t.{key} IS NULL) FROM demo_{i} s LEFT JOIN {table} t USING ({key});\n"
        sql += f"INSERT INTO {table} ({names}) SELECT {names} FROM demo_{i} s WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{key}=s.{key});\n"
    # Constraints are checked during INSERT; extra invariants catch broken projections.
    sql += "DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_5 p JOIN public.candidate_profiles c ON c.candidate_id=p.candidate_id WHERE NOT c.pool_consent OR NOT c.processing_consent) THEN RAISE EXCEPTION 'demo_transfer consent invariant'; END IF; END $$;\n"
    sql += "DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_5 p JOIN public.candidate_profiles c ON c.candidate_id=p.candidate_id WHERE NOT EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (candidate_id) candidate_id,state,source_revision,projected_revision FROM public.candidate_profile_outbox ORDER BY candidate_id,source_revision DESC,created_at DESC) o WHERE o.candidate_id=c.candidate_id AND o.state='delivered' AND o.source_revision=c.revision AND o.projected_revision=p.revision)) THEN RAISE EXCEPTION 'demo_transfer public source ACK invariant'; END IF; END $$;\n"
    sql += "COMMIT;\n" if apply else "ROLLBACK;\n"
    return sql


def backup(args):
    """Create a private target dump immediately before an explicit apply."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = f"/tmp/comit-before-demo-{stamp}.dump"
    shell = f"umask 077; docker exec {shlex.quote(args.target)} pg_dump -U comit -d comit -Fc > {shlex.quote(destination)} && test -s {shlex.quote(destination)}"
    if args.host:
        argv = ["ssh", "-i", args.identity, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-p", str(args.port), args.host, shell]
    else:
        argv = ["sh", "-c", shell]
    result = subprocess.run(argv, capture_output=True)
    if result.returncode:
        raise RuntimeError("private target backup failed; import cancelled")
    print("private_backup=" + destination)


def main():
    """Run dry-run by default, or an explicitly requested apply."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="comit-prerelease-postgres-1")
    parser.add_argument("--target", required=True)
    parser.add_argument("--host")
    parser.add_argument("--identity")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.host and not args.identity:
        parser.error("--identity required with --host")
    if args.source == args.target and not args.host:
        parser.error("source and target must differ")
    columns, data = export(command(args.source))
    if args.apply:
        backup(args)
    result = run(command(args.target, args if args.host else None), transfer_sql(columns, data, args.apply))
    print("mode=" + ("apply" if args.apply else "dry-run rollback"))
    print("table|source_rows|new_rows")
    print(result.strip())


if __name__ == "__main__":
    main()
