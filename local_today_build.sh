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
  # 생성물 로컬 변경 폐기 후, 항상 main 브랜치로 강제 재부착 + origin 정렬.
  #   detached HEAD(과거 수동 git 작업 잔재)·stale main 때문에 저녁 빌드가 통째로
  #   푸시 실패하던 문제의 근본 해결 — 매 빌드가 origin/main HEAD에서 새로 시작한다.
  #   (소스는 origin에 있으므로 안 건드림. 생성물은 아래 today.py가 재생성.)
  git checkout -- data/ 2>/dev/null || true
  git fetch origin main -q 2>/dev/null || echo "(fetch 경고 — 계속)"
  git checkout -B main origin/main 2>/dev/null || echo "⚠️ main 재부착 실패(수동 확인 필요)"

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
    pushed=0
    for pi in 1 2 3; do
      if git push origin main; then pushed=1; echo "푸시 완료"; break; fi
      echo "  (푸시 재시도 ${pi} — 원격 갱신 반영 후)"
      git pull --rebase -X theirs origin main 2>/dev/null || true
    done
    [ "$pushed" = 1 ] || echo "⚠️ 푸시 실패(재시도 소진 — 인증/원격 확인)"
  fi
  echo "$(date '+%F %T') === 종료 ==="
} >> "$LOG" 2>&1
