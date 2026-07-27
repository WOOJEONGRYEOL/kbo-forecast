#!/usr/bin/env bash
# statiz 시즌 데이터 갱신 스크립트
#
# 사용법:
#   ./update.sh            # 올해(현재 시즌) 갱신
#   ./update.sh 2027       # 특정 연도 갱신 (내년 새 시즌도 이렇게)
#
# - --skip-existing 을 쓰지 않으므로 해당 연도 파일을 새 데이터로 "덮어쓴다".
# - 규정본(batter_YYYY.csv)과 전체본(batter_YYYY_all.csv)을 모두 갱신.
# - 규정타석/이닝 기준은 팀 경기수에서 자동 계산되므로 시즌 진행도에 맞춰 조정됨.
set -e
cd "$(dirname "$0")"
source ../.venv_statiz/bin/activate
source ~/.statiz_env   # STATIZ_ID / STATIZ_PW

YEAR="${1:-$(date +%Y)}"
echo "==== [$YEAR] 규정본 갱신 ===="
python statiz_crawl.py crawl --years "$YEAR" --delay 8 --retries 3

echo "==== [$YEAR] 전체본(규정 이하 포함) 갱신 ===="
python statiz_crawl.py crawl --years "$YEAR" --all-players --tag all --delay 8 --retries 3

echo "==== [$YEAR] Google Sheets 업로드 ===="
# 이번 연도 파일만 시트에 반영(존재하는 것만). 자격증명 없으면 자동 스킵.
python push_to_sheets.py \
    "output/csv/batter_${YEAR}.csv" \
    "output/csv/pitcher_${YEAR}.csv" \
    "output/csv/team_batting_${YEAR}.csv" \
    "output/csv/team_pitching_${YEAR}.csv" \
    "output/csv/batter_${YEAR}_all.csv" \
    "output/csv/pitcher_${YEAR}_all.csv" || true

echo "==== [$YEAR] 갱신 완료 ===="
ls -la output/csv/*_"$YEAR"*.csv 2>/dev/null || true
