# -*- coding: utf-8 -*-
"""
main.py — KBO 단기 전력 예측 파이프라인 실행 진입점
=====================================================

사용법:
    python main.py                     # 기본: 2026 시즌, 최근 10경기
    python main.py --window 15        # 최근 15경기 기준으로 변경
    python main.py --season 2026      # 시즌 지정

파이프라인 순서 (E-T-M 구조):
    [Extraction]     네이버 API에서 경기결과 + kbostuff에서 세이버 지표 수집
    [Transformation] 팀별 경기로그 가공, 최근 N경기 rolling 집계
    [Modeling]       피타고리안 기대승률 → 괴리율 → 종합 모멘텀 지수
"""

import argparse
import sys
from pathlib import Path

# src/ 폴더의 모듈을 import할 수 있게 경로를 추가합니다
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import backtest
import boxscore
import config
import dashboard
import kbostuff_client
import model
import naver_games
import report


def main() -> None:
    # ── 명령줄 옵션 파싱 ──
    parser = argparse.ArgumentParser(description="KBO 단기 전력 예측")
    parser.add_argument("--season", type=int, default=config.SEASON,
                        help=f"분석 시즌 (기본 {config.SEASON})")
    parser.add_argument("--window", type=int, default=config.ROLLING_WINDOW,
                        help=f"최근 N경기 윈도우 (기본 {config.ROLLING_WINDOW})")
    parser.add_argument("--backtest", action="store_true",
                        help="피타고리안 예측력 백테스트/캘리브레이션만 실행")
    args = parser.parse_args()

    # 백테스트 모드: 경기 로그만 있으면 되므로 여기서 끝냅니다
    if args.backtest:
        games = naver_games.fetch_season_games(args.season)
        games = naver_games.filter_regular_season(games)
        team_log = naver_games.build_team_game_log(games)
        backtest.print_report(team_log)
        return

    # ── 1단계: 경기 결과 수집 ──
    print(f"\n[1/4] {args.season} 시즌 경기 결과 수집 (네이버 스포츠 API)")
    games = naver_games.fetch_season_games(args.season)
    games = naver_games.filter_regular_season(games)  # 시범경기 제거
    team_log = naver_games.build_team_game_log(games)
    n_games = len(team_log) // 2  # 행 2개 = 경기 1개
    print(f"  → 완료된 경기 {n_games}개, 팀 로그 {len(team_log)}행")

    # ── 2단계: 세이버 지표 수집 ──
    print("\n[2/4] 세이버 지표 수집 (kbostuff.app Supabase API)")
    players = kbostuff_client.fetch_players()
    print(f"  → 선수 명단 {len(players)}명 (투수-팀 매핑용)")
    pitching = kbostuff_client.team_pitching_score(args.season, players)
    print(f"  → 팀 투수진 K-Stuff+ 집계 완료 ({len(pitching)}팀)")
    batting = kbostuff_client.team_batting_score(args.season)
    print(f"  → 팀 타선 지표 집계 완료 ({len(batting)}팀)")
    team_fcb = kbostuff_client.team_fcb_score(args.season)
    print(f"  → 팀 FCB 승리기여 집계 완료 ({len(team_fcb)}팀)")

    # 선발 로테이션: 박스스코어에서 선발 식별 → 선발 K-Stuff+ + 아스널
    box = boxscore.collect_season_pitching(games)
    rotation = boxscore.identify_rotation(box)
    pitching_rot, rotation_detail = kbostuff_client.team_rotation(
        args.season, rotation)
    print(f"  → 선발 로테이션 식별 완료 ({len(rotation_detail)}팀, "
          f"투수 {len(rotation)}명)")

    # ── 3단계: 모델 계산 ──
    print(f"\n[3/4] 모델 계산 (최근 {args.window}경기 rolling 피타고리안)")
    pythag = model.rolling_pythagorean(team_log, window=args.window)
    season_sum = model.season_summary(team_log)
    result = model.combine(pythag, season_sum, pitching, batting, team_fcb,
                           pitching_rot)

    # ── 4단계: 리포트 (콘솔 + CSV + HTML 대시보드) ──
    print("\n[4/4] 리포트 생성")
    report.print_report(result, window=args.window)
    csv_path = report.save_csv(result)
    print(f"CSV 저장 완료 → {csv_path}")

    # 대시보드는 원시 경기 로그를 받아 JS에서 임의 윈도우로 즉석 계산합니다
    # (슬라이더로 경기 수를 자유롭게 바꾸는 인터랙션을 위해)
    html_path = dashboard.save_dashboard(result, team_log, window=args.window,
                                         rotation_detail=rotation_detail)
    print(f"대시보드 저장 완료 → {html_path}")
    print("  (브라우저에서 열기: open " + str(html_path) + ")\n")


if __name__ == "__main__":
    main()
