#!/usr/bin/env bash
# PlayerNo 백필 오버나잇 러너.
#  - rate 창이 풀리도록 라운드 사이를 길게(기본 2.5h) 쉬면서,
#    규정본 + 전체본의 남은 연도를 조금씩 이어받는다(--skip-existing = PlayerNo 유무 판단).
#  - 규정본·전체본이 모두 45/45가 되면 자동 종료.
cd "$(dirname "$0")"
source ../.venv_statiz/bin/activate
source ~/.statiz_env
LOG=output/overnight.log
FIRST_WAIT=${1:-10800}   # 첫 라운드 전 대기(기본 3h)
GAP=${2:-9000}           # 라운드 사이 대기(기본 2.5h)
MAX_ROUNDS=${3:-12}

remaining() {
  python - <<'PY'
import glob,pandas as pd
def hc(f):
    try:return "PlayerNo" in pd.read_csv(f,nrows=1).columns
    except:return False
yrs=[str(y) for y in range(1982,2027)]
reg=sum(1 for y in yrs if hc(f"output/csv/batter_{y}.csv") and hc(f"output/csv/pitcher_{y}.csv"))
al =sum(1 for y in yrs if hc(f"output/csv/batter_{y}_all.csv") and hc(f"output/csv/pitcher_{y}_all.csv"))
print((45-reg)+(45-al))
PY
}

echo "$(date '+%F %T') 오버나잇 백필 시작 (첫대기 ${FIRST_WAIT}s, 간격 ${GAP}s)" >> "$LOG"
sleep "$FIRST_WAIT"

for i in $(seq 1 "$MAX_ROUNDS"); do
  echo "$(date '+%F %T') === 라운드 $i: 규정본 ===" >> "$LOG"
  python statiz_crawl.py crawl --from-year 1982 --to-year 2026 \
      --skip-existing --delay 10 --retries 2 --retry-wait 45 >> "$LOG" 2>&1
  echo "$(date '+%F %T') === 라운드 $i: 전체본 ===" >> "$LOG"
  python statiz_crawl.py crawl --from-year 1982 --to-year 2026 --all-players --tag all \
      --skip-existing --delay 10 --retries 2 --retry-wait 45 >> "$LOG" 2>&1

  REM=$(remaining)
  echo "$(date '+%F %T') 라운드 $i 종료. 남은 파일세트: $REM" >> "$LOG"
  if [ "$REM" -eq 0 ]; then
    echo "$(date '+%F %T') ★ 전부 완료(규정본·전체본 45/45). 종료." >> "$LOG"
    break
  fi
  sleep "$GAP"
done
echo "$(date '+%F %T') 오버나잇 백필 러너 종료" >> "$LOG"
