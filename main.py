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

import pandas as pd

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
import standings_sim


def main() -> None:
    # ── 명령줄 옵션 파싱 ──
    parser = argparse.ArgumentParser(description="KBO 단기 전력 예측")
    parser.add_argument("--season", type=int, default=config.SEASON,
                        help=f"분석 시즌 (기본 {config.SEASON})")
    parser.add_argument("--window", type=int, default=config.ROLLING_WINDOW,
                        help=f"최근 N경기 윈도우 (기본 {config.ROLLING_WINDOW})")
    parser.add_argument("--backtest", action="store_true",
                        help="피타고리안 예측력 백테스트/캘리브레이션만 실행")
    parser.add_argument("--skill-backtest", action="store_true",
                        help="구위 항의 예측력을 여러 시즌으로 검증 "
                             "(모멘텀 가중치 근거)")
    parser.add_argument("--matchup-backtest", action="store_true",
                        help="선발 매치업(구위차)이 팀 폼보다 개별 경기를 "
                             "잘 맞추는지 검증 (당일 카드 go/no-go)")
    parser.add_argument("--standings-sim", action="store_true",
                        help="시즌 최종 순위 몬테카를로 시뮬 (잔여 매치업 복원)")
    parser.add_argument("--seasons", type=int, nargs="+",
                        default=[2021, 2022, 2023, 2024, 2025],
                        help="--skill-backtest에 쓸 시즌들 "
                             "(기본: 스케일이 안정적인 2021~2025)")
    args = parser.parse_args()

    # 백테스트 모드: 경기 로그만 있으면 되므로 여기서 끝냅니다
    if args.backtest:
        games = naver_games.fetch_season_games(args.season)
        games = naver_games.filter_regular_season(games)
        games = naver_games.filter_official_teams(games)
        team_log = naver_games.build_team_game_log(games)
        backtest.print_report(team_log)
        return

    # 구위 항 백테스트: 여러 시즌을 모아야 표본이 의미 있어집니다.
    # 팀 귀속은 게임로그만으로 복원하므로 박스스코어를 새로 받지 않습니다.
    if args.skill_backtest:
        logs, bgs = [], []
        for s in args.seasons:
            g = naver_games.filter_official_teams(
                naver_games.filter_regular_season(
                    naver_games.fetch_season_games(s)))
            tl = naver_games.build_team_game_log(g)
            bg = kbostuff_client.team_stuff_by_game_inferred(
                kbostuff_client.fetch_pitching_game_log(s))
            # 시즌이 섞이지 않도록 팀 코드에 시즌을 붙여 구분합니다
            logs.append(tl.assign(team=tl["team"] + f"_{s}"))
            bgs.append(bg.assign(team=bg["team"] + f"_{s}"))
        TL = pd.concat(logs, ignore_index=True)
        BG = pd.concat(bgs, ignore_index=True)
        backtest.print_skill_report(
            TL, BG, windows=(10, 15, 20),
            label=f"— {min(args.seasons)}~{max(args.seasons)} 통합",
        )
        backtest.print_horizon_report(TL, BG)
        return

    # 선발 매치업 백테스트: 경기별 선발이 필요하므로 박스스코어를 씁니다.
    if args.matchup_backtest:
        for s in args.seasons:
            g = naver_games.filter_official_teams(
                naver_games.filter_regular_season(
                    naver_games.fetch_season_games(s)))
            box = boxscore.collect_season_pitching(g)
            gl = kbostuff_client.fetch_pitching_game_log(s)
            backtest.print_matchup_report(g, box, gl, label=f"— {s}")
        return

    # 최종 순위 시뮬: 경기 결과만 있으면 됩니다.
    if args.standings_sim:
        games = naver_games.filter_official_teams(
            naver_games.filter_regular_season(
                naver_games.fetch_season_games(args.season)))
        team_log = naver_games.build_team_game_log(games)
        table = standings_sim.run(games, team_log)
        report.print_standings_sim(table, args.season)
        return

    # ── 1단계: 경기 결과 수집 ──
    print(f"\n[1/4] {args.season} 시즌 경기 결과 수집 (네이버 스포츠 API)")
    games = naver_games.fetch_season_games(args.season)
    games = naver_games.filter_regular_season(games)  # 시범경기 제거
    games = naver_games.filter_official_teams(games)  # 올스타전 등 제외
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
    # 최종 순위 시뮬 (잔여 매치업 복원 + 몬테카를로)
    sim_table = standings_sim.run(games, team_log)
    print(f"  → 최종 순위 시뮬 완료 (1위 유력: "
          f"{config.TEAM_NAMES.get(sim_table.index[0], sim_table.index[0])} "
          f"{sim_table.iloc[0]['p_first']*100:.0f}%)")

    # ── 4단계: 리포트 (콘솔 + CSV + HTML 대시보드) ──
    print("\n[4/4] 리포트 생성")
    report.print_report(result, window=args.window)
    csv_path = report.save_csv(result)
    print(f"CSV 저장 완료 → {csv_path}")

    # 대시보드는 원시 경기 로그를 받아 JS에서 임의 윈도우로 즉석 계산합니다
    # (슬라이더로 경기 수를 자유롭게 바꾸는 인터랙션을 위해)
    html_path = dashboard.save_dashboard(result, team_log, window=args.window,
                                         rotation_detail=rotation_detail,
                                         standings=sim_table)
    print(f"대시보드 저장 완료 → {html_path}")
    print("  (브라우저에서 열기: open " + str(html_path) + ")\n")


if __name__ == "__main__":
    main()
