#!/usr/bin/env bash
# launchd 가 매일 호출하는 자동 갱신 래퍼.
#  - 오프시즌(12~2월)에는 건너뛴다 (KBO 시즌: 3~11월).
#  - 시즌 중이면 update.sh 로 올해 데이터를 갱신한다.
cd "$(dirname "$0")"
mkdir -p output
LOG="output/cron.log"
MONTH=$(date +%-m)

if [ "$MONTH" -eq 12 ] || [ "$MONTH" -eq 1 ] || [ "$MONTH" -eq 2 ]; then
    echo "$(date '+%F %T') 오프시즌(${MONTH}월) - 자동 갱신 건너뜀" >> "$LOG"
    exit 0
fi

RUNLOG=$(mktemp)
{
    echo "$(date '+%F %T') === 자동 갱신 시작 ==="
    ./update.sh
    echo "$(date '+%F %T') === 자동 갱신 종료 ==="
} > "$RUNLOG" 2>&1

cat "$RUNLOG" >> "$LOG"

# 이번 실행 로그를 지메일로 발송(광고/브라우저 잡음 제거한 요약)
grep -vE "FETCH|SCRAPE|COMPLETE|CAPTURE|INIT|pubmatic|rubicon|doubleclick|gliacloud|pixel|truvid|nexx|amazon|linkedin" "$RUNLOG" \
    | python send_log_email.py >> "$LOG" 2>&1

rm -f "$RUNLOG"
