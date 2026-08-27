#!/usr/bin/env bash
# 프리게임 창 동안 맥이 idle-sleep으로 잠들지 않게 붙잡는다(암호 불필요).
# launchd가 창 시작 시각에 이 스크립트를 띄우고, 그때부터 창 끝까지 깨어 있게 함.
#   주의: 이미 잠든/꺼진 맥을 '깨우진' 못한다(그건 pmset repeat wakeorpoweron, 암호 필요).
h=$(date +%H)
if [ "$h" -lt 15 ]; then
  # 오후 창(12:50 기동): 저녁 창 끝까지 '이어붙여' 깨움 유지 → 12:45 깨우기 한 번으로
  # 오후 2시 경기 + 저녁 경기를 모두 커버(중간에 다시 잠들지 않게 bridge).
  /usr/bin/caffeinate -i -t 25200   # ≈19:50까지(약 7시간)
else
  /usr/bin/caffeinate -i -t 10800   # 저녁만 기동됐을 때 대비 → ~3시간(≈19:50까지)
fi
