#!/usr/bin/env bash
# 로컬 맥(launchd)에서 저녁 프리게임 창에 today.py를 돌려 라인업 반영 예측을
# '경기 전에' 갱신·푸시한다. GitHub 크론이 이 좁은 창(라인업 발표~첫 구)을
# 스로틀링으로 놓치는 문제의 주 해결책(로컬은 지연 없음).
#   생성물: data/today.html · predictions.json · predictions.html
set -o pipefail
FC="$HOME/kbo-forecast"
PY="$FC/.venv/bin/python"
cd "$FC" || exit 0
[ -f "$HOME/.statiz_env" ] && source "$HOME/.statiz_env"   # 있으면 환경 로드
mkdir -p "$FC/statiz_crawler/output"
LOG="$FC/statiz_crawler/output/today.log"

{
  echo "$(date '+%F %T') === today 빌드 시작 ==="
  # 생성물 로컬 변경은 폐기(원격/재생성이 최신) → pull 충돌 방지. 소스는 안 건드림.
  git checkout -- data/ 2>/dev/null || true
  git pull --rebase --autostash origin main || echo "(pull 경고 — 계속)"
  if git ls-files -u | grep -q .; then
    echo "(병합 충돌 → 원격 data/ 기준 정리)"
    git checkout --theirs -- data/ 2>/dev/null; git add data/ 2>/dev/null
    git rebase --continue 2>/dev/null || git merge --abort 2>/dev/null || true
  fi

  # 네트워크(DNS) 준비 대기 + 재시도 — launchd가 네트워크 채 안 올라온 시점에
  #   실행되거나 일시적 DNS 해석 실패로 today.py가 죽는 것을 방지.
  ok=0
  for attempt in 1 2 3 4; do
    if ! "$PY" -c "import socket; socket.gethostbyname('api-gw.sports.naver.com')" 2>/dev/null; then
      echo "  (DNS 미준비 ${attempt}/4 — 90s 대기 후 재시도)"; sleep 90; continue
    fi
    if "$PY" today.py; then ok=1; break; fi
    echo "  (today.py 실패 ${attempt}/4 — 60s 대기 후 재시도)"; sleep 60
  done
  [ "$ok" = 1 ] || { echo "⚠️ today.py 실패(재시도 소진 — DNS/네트워크 확인)"; exit 0; }
  git checkout -- data/last_updated.json 2>/dev/null || true   # today.py는 안 쓰는 파일

  git add -f data/today.html data/predictions.json data/predictions.html
  if git diff --cached --quiet; then
    echo "변경 없음 — 커밋 생략"
  else
    git commit -m "chore: today 라인업 반영 갱신 (로컬 $(date +%FT%H:%M))" \
      || { echo "⚠️ 커밋 실패"; exit 0; }
    git push origin main && echo "푸시 완료" \
      || echo "⚠️ 푸시 실패(키체인 잠김/원격 갱신 확인)"
  fi
  echo "$(date '+%F %T') === 종료 ==="
} >> "$LOG" 2>&1
