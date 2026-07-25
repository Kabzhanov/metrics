#!/usr/bin/env bash
# git-capture.sh — захват git diff в run_artifacts (задача #1176, фаза 5.5)
#
# Использование:
#   bash git-capture.sh --project-id 5 --base-sha HEAD~5 --head-sha HEAD [--run-id 123]
#
# Что делает:
#   1. Запускает git_integration.py и получает {file: (add, del)}
#   2. Если есть --run-id: INSERT по одной строке в run_artifacts
#      (artifact_type='git_diff', path=file, size=add+del, metadata={add,del})
#   3. Пересчитывает run_evaluations.reopened: TRUE, если файл правился
#      в последние 30 дней (т.е. работа не "застабилизировалась")
#
# БД: bizdnai :5434 (таблицы Phase 1: projects, task_runs, run_artifacts, run_evaluations)
# Без --run-id скрипт работает в "dry" режиме (только показывает diff).

set -euo pipefail

PROJECT_ID=""
BASE_SHA=""
HEAD_SHA=""
RUN_ID=""
REPO_PATH="/home/bizdnai/planet"
MAX_FILES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-id) PROJECT_ID="$2"; shift 2 ;;
        --base-sha)   BASE_SHA="$2";   shift 2 ;;
        --head-sha)   HEAD_SHA="$2";   shift 2 ;;
        --run-id)     RUN_ID="$2";     shift 2 ;;
        --repo)       REPO_PATH="$2";  shift 2 ;;
        --max-files)  MAX_FILES="$2";  shift 2 ;;
        --help|-h)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$PROJECT_ID" || -z "$BASE_SHA" || -z "$HEAD_SHA" ]]; then
    echo "Usage: $0 --project-id N --base-sha X --head-sha Y [--run-id R] [--repo PATH]" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${SCRIPT_DIR}/git_integration.py"

if [[ ! -x "$PY" && ! -f "$PY" ]]; then
    echo "Не найден $PY" >&2
    exit 3
fi

MAX_ARGS=()
if [[ -n "$MAX_FILES" ]]; then
    MAX_ARGS=(--max-files "$MAX_FILES")
fi

echo "[git-capture] project=$PROJECT_ID repo=$REPO_PATH range=$BASE_SHA..$HEAD_SHA"

# 1. получить JSON от Python
DIFF_JSON="$(python3 "$PY" "$REPO_PATH" "$BASE_SHA..$HEAD_SHA" \
    --project-id "$PROJECT_ID" --json ${MAX_ARGS[@]:-})"

FILES_COUNT=$(echo "$DIFF_JSON" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['files_changed'])")
ADD=$(echo "$DIFF_JSON" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['additions'])")
DEL=$(echo "$DIFF_JSON" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['deletions'])")

echo "[git-capture] files=$FILES_COUNT +$ADD -$DEL"

# если dry-режим — выходим после саммари
if [[ -z "$RUN_ID" ]]; then
    echo "[git-capture] --run-id не задан, dry-режим (только саммари)"
    exit 0
fi

# 2. INSERT в run_artifacts (одна строка на файл)
INSERTED=0
while IFS=$'\t' read -r path add del; do
    [[ -z "$path" ]] && continue
    META=$(python3 -c "import json;print(json.dumps({'add':$add,'del':$del,'base_sha':'$BASE_SHA','head_sha':'$HEAD_SHA'}))")
    SIZE=$((add + del))
    PGPASSWORD=bizdnai psql -h localhost -p 5434 -U bizdnai -d bizdnai -q -c \
        "INSERT INTO run_artifacts (run_id, artifact_type, path, size, metadata)
         VALUES ($RUN_ID, 'git_diff', $(printf '%s' "$path" | sed "s/'/''/g" | python3 -c "import sys;print('\''+sys.stdin.read().strip()+'\'')"),
                 $SIZE, '$META'::jsonb)
         ON CONFLICT DO NOTHING;" >/dev/null
    INSERTED=$((INSERTED + 1))
done < <(echo "$DIFF_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for f, (a, x) in d['files'].items():
    print(f'{f}\t{a}\t{x}')
")

echo "[git-capture] inserted=$INSERTED rows into run_artifacts"

# 3. пересчёт run_evaluations.reopened
# TRUE, если хотя бы один из изменённых файлов был снова отредактирован
# в последние 30 дней (git log -1 --since='30 days ago').
LAST_TOUCH=$(cd "$REPO_PATH" && git log -1 --format='%cI' --since='30 days ago' 2>/dev/null || true)
if [[ -n "$LAST_TOUCH" ]]; then
    PGPASSWORD=bizdnai psql -h localhost -p 5434 -U bizdnai -d bizdnai -q -c \
        "INSERT INTO run_evaluations (run_id, reopened, stable_after_30d)
         VALUES ($RUN_ID, TRUE, FALSE)
         ON CONFLICT (run_id) DO UPDATE
           SET reopened = TRUE,
               stable_after_30d = FALSE
                  WHERE run_evaluations.run_id = $RUN_ID
                    AND run_evaluations.stable_after_30d IS NOT FALSE;" >/dev/null
    echo "[git-capture] run_evaluations.reopened = TRUE (last touch: $LAST_TOUCH)"
fi

echo "[git-capture] DONE"
