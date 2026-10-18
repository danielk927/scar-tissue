#!/usr/bin/env bash
# IC-2 smoke test (SPEC 10): generation 1 of lineage D-s$SEED (default seed 9) in tiny mode (traffic 20, failure cap 5, 2 variants,
# K_SAMPLES 2, first 30 eval_v1 tasks). Resets D-s$SEED in Person 3's tables first (its generations, model versions other
# than base, training examples), runs the generation through train POST /loop/run, polls /loop/status and prints the
# GenerationRecord. Passes when it ends promoted or rejected with a registered candidate and examples_built > 0.
# Tiny results are never reported. The default seed 9 keeps tiny runs off the real lineages (seeds 0-2): the agent
# keeps the tiny traffic runs and failure records in app.runs/app.failures, and a real D-s0-g1 would reuse them.
#   scripts/smoke_ic2.sh [--yes]     --yes skips the reset confirmation
# TRAIN_SERVICE_URL comes from the environment or the repo-root .env. Needs curl, python3 and uv.
set -euo pipefail

SEED="${SEED:-9}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

env_value() { # NAME DEFAULT: the environment, else the repo-root .env, else DEFAULT
  local value="${!1:-}"
  if [[ -z "$value" && -f "$ROOT/.env" ]]; then
    value="$(grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//')"
  fi
  echo "${value:-$2}"
}
fail() { echo "FAIL: $*" >&2; exit 1; }
field() { # KEY < JSON: the value, or the last element of a list ("" if empty)
  python3 -c 'import json, sys; v = json.load(sys.stdin)[sys.argv[1]]; print((v[-1] if v else "") if isinstance(v, list) else v)' "$1"
}

case "${1:-}" in
  --yes) YES=1 ;;
  "") YES=0 ;;
  *) echo "usage: $0 [--yes]" >&2; exit 2 ;;
esac

TRAIN_URL="$(env_value TRAIN_SERVICE_URL http://localhost:8003)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
curl -fsS --max-time 5 "$TRAIN_URL/health" >/dev/null || fail "train service is not healthy at $TRAIN_URL/health"

# The reset is for tiny leftovers: never delete full-size results or a generation a job may still be running.
curl -fsS --max-time 10 "$TRAIN_URL/generations?arm=D&seed=$SEED" >"$WORK/lineage.json" || fail "train GET /generations failed"
python3 - "$WORK/lineage.json" "$SEED" <<'PY' || exit 1
import json, sys
recs, seed = json.load(open(sys.argv[1])), sys.argv[2]
full = [g["generation_id"] for g in recs if not g["notes"].startswith("[tiny]")]
busy = [f"{g['generation_id']} ({g['status']})" for g in recs if g["status"] not in ("promoted", "rejected", "failed")]
if full:
    sys.exit(f"FAIL: D-s{seed} has full-size records ({', '.join(full)}); IC-2 would delete them (reset by hand if you mean it)")
if busy:
    sys.exit(f"FAIL: {', '.join(busy)} may still be running; wait for its job, or after a train restart reset by hand:"
             f" uv run --directory train python -m scar_train.loop reset-lineage D {seed}")
PY
if [[ "$YES" != 1 ]]; then
  read -r -p "Reset lineage D-s$SEED (delete its generations, model versions except base, training examples)? [y/N] " answer ||
    answer=""
  [[ "$answer" == [yY]* ]] || fail "aborted"
fi
uv run --directory "$ROOT/train" --quiet python -m scar_train.loop reset-lineage D "$SEED"

body="{\"arm\": \"D\", \"seed\": $SEED, \"generations\": [1], \"tiny\": true}"
curl -sS --fail-with-body --max-time 30 -H 'Content-Type: application/json' -d "$body" "$TRAIN_URL/loop/run" \
  >"$WORK/job.json" || fail "POST /loop/run: $(cat "$WORK/job.json")"
job_id="$(field job_id <"$WORK/job.json")"
echo "job $job_id: D-s$SEED-g1 tiny"

last=""
while true; do
  curl -sS --fail-with-body --max-time 10 "$TRAIN_URL/loop/status/$job_id" >"$WORK/status.json" ||
    fail "GET /loop/status/$job_id: $(cat "$WORK/status.json") (a train restart forgets jobs: re-run this script)"
  status="$(field status <"$WORK/status.json")"
  line="$(field log_tail <"$WORK/status.json")"
  if [[ "$line" != "$last" ]]; then
    echo "  $line"
    last="$line"
  fi
  if [[ "$status" == "done" || "$status" == "failed" ]]; then
    break
  fi
  sleep 10
done
if [[ "$status" == "failed" ]]; then
  echo "job failed; log tail:"
  python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)["log_tail"]))' <"$WORK/status.json"
fi

curl -sS --fail-with-body --max-time 10 "$TRAIN_URL/generations/D-s$SEED-g1" >"$WORK/generation.json" ||
  fail "GET /generations/D-s$SEED-g1: $(cat "$WORK/generation.json")"
curl -fsS --max-time 10 "$TRAIN_URL/models" >"$WORK/models.json" || fail "train GET /models failed"
echo "GenerationRecord D-s$SEED-g1:"
python3 -m json.tool "$WORK/generation.json"
python3 - "$WORK/generation.json" "$WORK/models.json" <<'PY'
import json, sys
rec = json.load(open(sys.argv[1]))
models = {m["version"] for m in json.load(open(sys.argv[2]))}
problems = []
if rec["status"] not in ("promoted", "rejected"):
    problems.append(f"status is {rec['status']}")
if rec["candidate_model_version"] not in models:
    problems.append(f"no registered candidate model (candidate_model_version {rec['candidate_model_version']})")
if rec["examples_built"] <= 0:
    problems.append("examples_built is 0")
if problems:
    sys.exit("FAIL: " + "; ".join(problems))
print(f"IC-2 smoke passed: {rec['generation_id']} {rec['status']}, candidate {rec['candidate_model_version']},"
      f" {rec['examples_built']} examples built (tiny mode: never report these numbers)")
PY
