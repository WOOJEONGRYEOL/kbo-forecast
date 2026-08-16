# -*- coding: utf-8 -*-
"""
today.py — '오늘의 기대 스코어'만 빠르게 재생성하는 경량 빌드
================================================================
전체 대시보드(main.py)와 달리 today.html 한 장만 다시 만든다.
저녁 라인업 발표(경기 ~30분 전)를 자주 반영하기 위한 용도로,
GitHub Actions에서 30분 간격으로 돌린다.

무거운 부분(박스스코어·타자 wRC+ CSV)은 data 캐시를 재사용하므로 저렴하다.
살아있는 신선한 부분은 네이버 preview(예고선발·발표 라인업)뿐이다.
라인업 CSV가 없으면(캐시 미스) 타순 배수는 1.0으로 자연 degrade한다.
"""
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, ".")

import config
import naver_games
import boxscore
import game_projection
import dashboard


def main():
    t0 = time.time()
    games = naver_games.fetch_season_games(config.SEASON)
    games = naver_games.filter_regular_season(games)
    games = naver_games.filter_official_teams(games)
    box = boxscore.collect_season_pitching(games)   # 캐시 히트 위주 → 빠름
    projections = game_projection.project_games(games, box)
    game_projection.save_prediction_log(projections, games)   # 예측+실제 결과 누적 저장
    path = dashboard.save_today_page(projections)
    ready = sum(1 for g in projections["games"] if g.get("lineupReady"))
    n = len(projections["games"])
    print(f"  → 오늘의 경기 {n}경기 (라인업 반영 {ready}/{n}) "
          f"[{projections['date']}] → {path}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
