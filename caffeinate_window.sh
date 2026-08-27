#!/usr/bin/env bash
# 프리게임 창 동안 맥이 idle-sleep으로 잠들지 않게 붙잡는다(암호 불필요).
# launchd가 창 시작 시각에 이 스크립트를 띄우고, 그때부터 창 끝까지 깨어 있게 함.
#   주의: 이미 잠든/꺼진 맥을 '깨우진' 못한다(그건 pmset repeat wakeorpoweron, 암호 필요).
h=$(date +%H)
if [ "$h" -lt 15 ]; then
  /usr/bin/caffeinate -i -t 5400    # 13시대 오후 창 → ~90분(≈14:20까지)
else
  /usr/bin/caffeinate -i -t 10800   # 저녁 창 → ~3시간(≈19:50까지)
fi
