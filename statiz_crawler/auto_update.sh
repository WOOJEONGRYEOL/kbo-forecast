#!/usr/bin/env bash
# launchd 가 매일 호출하는 자동 갱신 래퍼.
#  - 오프시즌(12~2월)에는 건너뛴다 (KBO 시즌: 3~11월).
#  - 시즌 중이면 update.sh 로 올해 데이터를 갱신한다.
cd "$(dirname "$0")"
[ -f ~/.statiz_env ] && source ~/.statiz_env   # 메일 앱 비밀번호(STATIZ_MAIL_*) 등 로드
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

    # ── 마지막 고리: 크롤 원본 → kbo-forecast 요약본 재생성 → 역대 페이지 갱신 → 커밋/푸시 ──
    # (이게 있어야 매일 크롤한 Statiz가 역대 탭·팀 스타일 등에 실제로 반영됨)
    echo "$(date '+%F %T') === kbo-forecast 반영 시작 ==="
    (
        FC="$HOME/kbo-forecast"
        cd "$FC" || { echo "(kbo-forecast 없음 — 스킵)"; exit 0; }
        PY="$FC/.venv/bin/python"
        # 생성물(data/*.html·요약 CSV)의 로컬 변경은 폐기 — 어차피 재생성/원격이 최신이고,
        # 이게 남아있으면 pull 시 autostash 충돌로 커밋이 막힌다. 소스(src/·*.py)는 건드리지 않음.
        git checkout -- data/ 2>/dev/null || true
        git pull --rebase --autostash origin main || echo "(pull 경고 — 계속)"
        # 병합 충돌이 남았으면(예외 상황) 원격 기준으로 정리
        if git ls-files -u | grep -q .; then
            echo "(병합 충돌 감지 → 원격 data/ 기준으로 정리)"
            git checkout --theirs -- data/ 2>/dev/null; git add data/ 2>/dev/null
            git rebase --continue 2>/dev/null || git merge --abort 2>/dev/null || true
        fi
        "$PY" build_history.py && "$PY" build_clusters.py && "$PY" build_statiz.py && "$PY" history.py
        git add data/history_batters.csv data/history_pitchers.csv \
                data/team_style_*.csv data/bullpen_*.csv data/statiz_*.csv data/history.html
        if git diff --cached --quiet; then
            echo "(요약본 변경 없음 — 커밋 생략)"
        else
            git commit -m "chore: Statiz 요약·역대 자동 갱신 ($(date +%F))" || { echo "⚠️ 커밋 실패 — git 상태 확인 필요"; exit 0; }
            git push origin main && echo "푸시 완료" || echo "⚠️ 푸시 실패 — 인증(키체인 잠김) 또는 원격 갱신 확인 필요"
        fi
    )
    echo "$(date '+%F %T') === kbo-forecast 반영 종료 ==="
} > "$RUNLOG" 2>&1

cat "$RUNLOG" >> "$LOG"

# 이번 실행 로그를 지메일로 발송(광고/브라우저 잡음 제거한 요약)
grep -vE "FETCH|SCRAPE|COMPLETE|CAPTURE|INIT|pubmatic|rubicon|doubleclick|gliacloud|pixel|truvid|nexx|amazon|linkedin" "$RUNLOG" \
    | python3 send_log_email.py >> "$LOG" 2>&1

rm -f "$RUNLOG"
