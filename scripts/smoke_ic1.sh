#!/usr/bin/env bash
# IC-1 smoke test (SPEC 10): services healthy, ledger loaded with row-level security, model version `base`
# registered, the 30 seed tasks run on base through agent /agent/run_batch (purpose "smoke"), every run stored with
# `passed` set by the verifier; prints the base pass rate. Sentry spans and issues are checked by hand.
#   scripts/smoke_ic1.sh
# DATABASE_URL and the service URLs come from the environment or the repo-root .env. Needs psql, curl and python3.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SEED_FILE="$ROOT/fixtures/tasks_seed.jsonl"

env_value() { # NAME DEFAULT: the environment, else the repo-root .env, else DEFAULT
  local value="${!1:-}"
  if [[ -z "$value" && -f "$ROOT/.env" ]]; then
    value="$(grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//')"
  fi
  echo "${value:-$2}"
}
fail() { echo "FAIL: $*" >&2; exit 1; }
section() { echo; echo "== $*"; }

DATABASE_URL="$(env_value DATABASE_URL postgresql://scar:scar@localhost:5432/scar)"
ENV_URL="$(env_value ENV_SERVICE_URL http://localhost:8001)"
AGENT_URL="$(env_value AGENT_SERVICE_URL http://localhost:8002)"
TRAIN_URL="$(env_value TRAIN_SERVICE_URL http://localhost:8003)"
PSQL=(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -X -tA)
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

section "health"
for pair in "env $ENV_URL" "agent $AGENT_URL" "train $TRAIN_URL"; do
  name="${pair%% *}" url="${pair#* }"
  curl -fsS --max-time 5 "$url/health" >/dev/null || fail "$name service is not healthy at $url/health"
  echo "ok   $name ($url)"
done

section "ledger"
for table in companies accounts vendors transactions; do
  n="$("${PSQL[@]}" -c "SELECT count(*) FROM ledger.$table")"
  [[ "$n" -gt 0 ]] || fail "ledger.$table is empty (load the ledger first)"
  echo "ok   ledger.$table: $n rows"
done

section "row-level security (role scar_sql_tool)"
company="$("${PSQL[@]}" -c "SELECT company_id FROM ledger.companies ORDER BY company_id LIMIT 1")"
# Same order as env's sql_query: set the company, then switch role, inside one transaction.
seen="$("${PSQL[@]}" <<SQL | grep '^rls ' || true
BEGIN;
SELECT set_config('app.company_id', '$company', true);
SET LOCAL ROLE scar_sql_tool;
SELECT 'rls ' || (SELECT count(DISTINCT company_id) FROM ledger.accounts) || ' '
  || (SELECT count(DISTINCT company_id) FROM ledger.vendors) || ' '
  || (SELECT count(DISTINCT company_id) FROM ledger.transactions);
ROLLBACK;
SQL
)"
[[ "$seen" == "rls 1 1 1" ]] || fail "scar_sql_tool with app.company_id=$company sees companies per table: '${seen#rls }'"
echo "ok   accounts, vendors and transactions show only $company"
denied="$("${PSQL[@]}" -c "BEGIN; SET LOCAL ROLE scar_sql_tool; SELECT count(*) FROM ledger.companies; ROLLBACK;" 2>&1 || true)"
grep -q "permission denied" <<<"$denied" || fail "scar_sql_tool can read ledger.companies: $denied"
echo "ok   ledger.companies is denied"

section "model version base"
curl -fsS --max-time 10 "$TRAIN_URL/models" >"$WORK/models.json" || fail "train GET /models failed"
python3 - "$WORK/models.json" <<'PY' || fail "base is not registered (uv run python -m scar_train.register_base ...)"
import json, sys
base = [m for m in json.load(open(sys.argv[1])) if m["version"] == "base"]
if not base:
    sys.exit(1)
print(f"ok   base: {base[0]['endpoint_model_id']} at {base[0]['endpoint_base_url']} ({base[0]['base_model_hf_id']})")
PY

section "30 seed tasks on base (purpose smoke)"
python3 - "$SEED_FILE" >"$WORK/request.json" <<'PY'
import json, sys
ids = [json.loads(line)["task_id"] for line in open(sys.argv[1]) if line.strip()]
requests = [{"task_id": t, "model_version": "base", "prompt_version": "system_v1", "purpose": "smoke"} for t in ids]
print(json.dumps({"requests": requests, "max_concurrency": 8}))
PY
# 30 runs at concurrency 8: four waves of at most 180 s each, plus one spare (clients.batch_timeout).
curl -sS --fail-with-body --max-time 900 -H 'Content-Type: application/json' -d "@$WORK/request.json" \
  "$AGENT_URL/agent/run_batch" >"$WORK/runs.json" || fail "agent /agent/run_batch: $(cat "$WORK/runs.json")"
python3 - "$WORK/runs.json" "$SEED_FILE" "$WORK/run_ids" <<'PY' || fail "the batch did not return one judged run per seed task"
import json, sys
from collections import Counter
runs = json.load(open(sys.argv[1]))
want = [json.loads(line)["task_id"] for line in open(sys.argv[2]) if line.strip()]
if [r["task_id"] for r in runs] != want:
    sys.exit(f"expected {len(want)} runs in seed order, got {len(runs)}")
if any(not isinstance(r.get("passed"), bool) for r in runs):
    sys.exit("some runs have no boolean `passed`")
for r in runs:
    print(f"{'PASS' if r['passed'] else 'fail'} {r['task_id']} {r['family']:<11} {r['stop_reason']:<11} {r['verify_detail'][:70]}")
passed = sum(r["passed"] for r in runs)
print("stop reasons:", dict(sorted(Counter(r["stop_reason"] for r in runs).items())))
print(f"base pass rate: {passed}/{len(runs)} = {passed / len(runs):.1%}")
open(sys.argv[3], "w").write("{" + ",".join(r["run_id"] for r in runs) + "}")
PY
stored="$("${PSQL[@]}" -c "SELECT count(*) FROM app.runs WHERE run_id = ANY('$(cat "$WORK/run_ids")') AND purpose = 'smoke'")"
[[ "$stored" == "30" ]] || fail "only $stored of 30 runs are stored in app.runs"
echo "ok   all 30 runs stored in app.runs with passed set by the verifier"

section "check Sentry by hand"
echo "- Traces: one 'invoke_agent ledger-agent' trace per smoke run (filter purpose:smoke), with gen_ai.chat and"
echo "  execute_tool spans and the run tags (run_id, task_id, template_id, passed, stop_reason)."
echo "- Issues: failed runs grouped as 'agent_run_failed' issues by template and stop reason. The agent raises them"
echo "  only for purpose traffic and judge (FAILURE_EVENT_PURPOSES), so check them after a traffic or judge failure."
echo
echo "IC-1 smoke passed (Sentry checks above are manual)."
