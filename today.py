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
    config.SEASON = naver_games.resolve_season()   # 시즌 자동감지(개막 첫 경기부터 전환)
    raw = naver_games.fetch_season_games(config.SEASON)        # 포스트시즌 포함(필터 전)
    # 진행 중인 달 라이브 수집이 실패(캐시 폴백)했으면 데이터가 stale이다. 이때 라이브
    #   페이지를 재생성·커밋하면 더 신선한 커밋(국내 IP 로컬 빌더)을 '빈/과거' 상태로
    #   덮어써 포스트시즌 기록이 사라지는 퇴행이 난다. 네이버가 해외/클라우드 IP를
    #   드롭해 GitHub Actions는 항상 여기 걸린다 → 아무것도 커밋 안 함(배포만).
    if not naver_games.LIVE_MONTH_FETCH_OK:
        print("  [중단] 진행 중인 달 라이브 수집 실패(stale) → 라이브 페이지 갱신 건너뜀 "
              f"(퇴행 방지)  ({time.time() - t0:.1f}s)")
        return
    games = naver_games.filter_official_teams(naver_games.filter_regular_season(raw))
    box = boxscore.collect_season_pitching(games)   # 캐시 히트 위주 → 빠름
    projections = game_projection.project_games(games, box)
    game_projection.save_prediction_log(projections, games)   # 예측+실제 결과 누적 저장
    dashboard.save_predictions_page()                          # 예측 성적표(누적)
    path = dashboard.save_today_page(projections)
    # 가을야구: 포스트시즌 경기가 피드에 있으면 대진·예측·시리즈확률 페이지 갱신(라이브).
    try:
        import postseason, postseason_dashboard
        st = postseason.build_postseason(games, raw, box)
        if st and any(r["games"] or r["lower"] for r in st["rounds"]):
            postseason_dashboard.save_postseason_page(st)
            print("  → 가을야구 페이지 갱신 → data/postseason.html")
    except Exception as e:
        print(f"  (가을야구 생성 건너뜀: {e})")
    ready = sum(1 for g in projections["games"] if g.get("lineupReady"))
    n = len(projections["games"])
    print(f"  → 오늘의 경기 {n}경기 (라인업 반영 {ready}/{n}) "
          f"[{projections['date']}] → {path}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
