#!/usr/bin/env bash
# Apply the shared DDL (schemas/scar_schemas/ddl.sql) to $DATABASE_URL (SPEC P3-02).
#   scripts/init_db.sh           apply once; refuses if the schemas already exist
#   scripts/init_db.sh --reset   drop the ledger and app schemas first (destroys all data in them)
# DATABASE_URL comes from the environment or the repo-root .env. The user must be able to create roles.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${DATABASE_URL:-}" && -f "$ROOT/.env" ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ROOT/.env" | tail -1 | cut -d= -f2-)"
fi
: "${DATABASE_URL:?set DATABASE_URL or add it to .env}"
PSQL=(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -X)

if [[ "${1:-}" == "--reset" ]]; then
  "${PSQL[@]}" -c "DROP SCHEMA IF EXISTS app CASCADE; DROP SCHEMA IF EXISTS ledger CASCADE;"
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--reset]" >&2
  exit 2
fi

if [[ "$("${PSQL[@]}" -tAc "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ('app', 'ledger')")" != "0" ]]; then
  echo "schemas app/ledger already exist; use --reset to recreate them (destroys their data)" >&2
  exit 1
fi

# The role is cluster-wide, so it may survive a --reset; create it only if missing.
sed 's/^CREATE ROLE scar_sql_tool NOLOGIN;$/DO $$ BEGIN CREATE ROLE scar_sql_tool NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;/' \
  "$ROOT/schemas/scar_schemas/ddl.sql" | "${PSQL[@]}"
# sql_query isolation: without this a query could call set_config to change company or role (docs/DECISIONS.md).
"${PSQL[@]}" -c "REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;"
echo "schema applied to $(echo "$DATABASE_URL" | sed -E 's#//[^@]*@#//#')"
