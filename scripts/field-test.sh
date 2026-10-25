#!/usr/bin/env bash
set -uo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REPO="${PHASECTL_FIELD_REPO:-$HOME/Desktop/contextos}"
OUTPUT="${PHASECTL_FIELD_OUTPUT:-$ROOT/.field-test}"
PYTHON="${PHASECTL_FIELD_PYTHON:-}"

usage() {
    printf 'Usage: %s [--repo PATH] [--output DIR]\n' "$0"
}

while (($#)); do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            REPO="$2"
            shift 2
            ;;
        --output)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            OUTPUT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$PYTHON" ]]; then
    if [[ -x "$ROOT/.venv/bin/python" ]]; then
        PYTHON="$ROOT/.venv/bin/python"
    else
        PYTHON="$(command -v python3)" || exit 127
    fi
fi

"$PYTHON" -c '
from pathlib import Path
import sys
repo = Path(sys.argv[1]).expanduser().resolve()
output = Path(sys.argv[2]).expanduser().resolve()
if output == repo or repo in output.parents:
    raise SystemExit("field-test output must not be inside the external repository")
if not repo.is_dir():
    raise SystemExit(f"external repository does not exist: {repo}")
' "$REPO" "$OUTPUT" || exit $?

mkdir -p "$OUTPUT" || exit $?
LOG="$OUTPUT/run.log"
RESULTS="$OUTPUT/results.json"
if [[ -e "$LOG" || -e "$RESULTS" ]]; then
    printf 'field-test output already exists: %s\n' "$OUTPUT" >&2
    exit 1
fi
START="$SECONDS"

printf 'phasectl field test\nrepository: %s\noutput: %s\npython: %s\ncommand: PYTHONPATH=%s/src %s -m pytest -q -p no:cacheprovider tests/field/test_field_runner.py\n' \
    "$REPO" "$OUTPUT" "$PYTHON" "$ROOT" "$PYTHON" | tee "$LOG"

PHASECTL_FIELD_REPO="$REPO" \
PHASECTL_FIELD_OUTPUT="$OUTPUT" \
PYTHONPATH="$ROOT/src" \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONHASHSEED=0 \
GIT_OPTIONAL_LOCKS=0 \
    "$PYTHON" -m pytest -q -p no:cacheprovider "$ROOT/tests/field/test_field_runner.py" 2>&1 | tee -a "$LOG"
STATUS=${PIPESTATUS[0]}

if [[ ! -f "$RESULTS" ]]; then
    STATUS=1
    "$PYTHON" -c '
import json
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(json.dumps({
    "schema_version": 1,
    "status": "failed",
    "error": "pytest exited before producing field-test results",
}, indent=2, sort_keys=True) + "\n")
' "$RESULTS" || STATUS=1
fi

DURATION=$((SECONDS - START))
printf 'results: %s\nlog: %s\nduration: %ss\nexit: %s\n' \
    "$RESULTS" "$LOG" "$DURATION" "$STATUS" | tee -a "$LOG"
exit "$STATUS"
