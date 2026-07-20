# -*- coding: utf-8 -*-
"""
report.py — 진단 리포트 출력
==============================

모델이 계산한 표를 사람이 읽기 좋은 콘솔 리포트로 찍고,
같은 내용을 CSV 파일로도 저장합니다 (엑셀에서 열어보기 좋게).
"""

from datetime import date
from pathlib import Path

import pandas as pd

import config


def print_standings_sim(table: pd.DataFrame, season: int) -> None:
    """시즌 최종 순위 몬테카를로 결과를 콘솔에 출력합니다."""
    played = int(table["played"].mean())
    pct = played / 144 * 100
    print("\n" + "=" * 92)
    print(f"  KBO {season} 최종 순위 시뮬레이션  —  진행률 {pct:.0f}% "
          f"(팀당 평균 {played}경기)")
    print("=" * 92)
    print(f"{'':2}{'팀':<12}{'현재승률':>8}{'피타강도':>8}{'잔여':>5}"
          f"{'예상최종':>9}{'1위%':>7}{'가을%':>7}{'순위(중앙)':>9}{'90%구간':>10}")
    print("-" * 92)
    for i, (team, r) in enumerate(table.iterrows(), 1):
        name = config.TEAM_NAMES.get(team, team)
        band = f"{r['rank_lo']}~{r['rank_hi']}"
        print(f"{i:>2}{name:<12}{r['cur_wpct']:>8.3f}{r['pyth']:>8.3f}"
              f"{int(r['remaining']):>5}{r['proj_wpct']:>9.3f}"
              f"{r['p_first']*100:>6.1f}%{r['p_playoff']*100:>6.1f}%"
              f"{int(r['rank_median']):>7}{band:>12}")
    print("-" * 92)
    print("""
[읽는 법]
  피타강도  득실점 기반 피타고리안 기대승률 = 팀 실력 추정치 (log5 시뮬 입력)
  예상최종  남은 경기를 강도대로 시뮬한 최종 승률의 평균 (20,000회)
  1위 / 가을%  정규시즌 1위 / 5위 안(가을야구) 진입 확률
  90%구간   시뮬 순위의 5~95% 범위. 좁으면 굳었고, 넓으면 아직 유동적

  ※ 잔여 매치업은 'KBO 팀당 상대별 16경기' 규칙으로 복원했습니다.
    일정(날짜)이 아직 안 나와도 순위 확률에는 영향이 없습니다.
  ※ 순위는 승률 서열이라 중위권(가을야구 경계)은 몇 승 차로 뒤집힙니다.
    단정하지 말고 확률·구간으로 보세요.
""")


def print_report(df: pd.DataFrame, window: int) -> None:
    """콘솔에 팀별 단기 전력 진단표를 출력합니다."""

    line = "=" * 100
    print()
    print(line)
    print(f"  KBO {config.SEASON} 단기 전력 진단  —  최근 {window}경기 기준"
          f"  (생성일: {date.today()})")
    print(line)

    # 헤더: 각 컬럼이 무엇인지 한 줄 설명
    print(f"{'순위':>2} {'팀':<8} {'최근성적':>9} {'실제승률':>7} {'기대승률':>7} "
          f"{'괴리율':>7} {'구위+':>6} {'타선+':>6} {'모멘텀':>7}  진단")
    print("-" * 100)

    for rank, (team, row) in enumerate(df.iterrows(), start=1):
        name = config.TEAM_NAMES.get(team, team)
        record = f"{row['recent_w']}승{row['recent_l']}패"
        if row["recent_d"] > 0:
            record += f"{row['recent_d']}무"

        # 괴리율은 부호가 중요하므로 +/- 를 명시합니다
        print(
            f"{rank:>2} {name:<8} {record:>9} "
            f"{row['actual_wpct']:>8.3f} {row['expected_wpct']:>8.3f} "
            f"{row['gap']:>+8.3f} "
            f"{row['team_stuff_plus']:>6.1f} {row['bat_wrc_pure']:>6.1f} "
            f"{row['momentum']:>+7.2f}  {row['diagnosis']}"
        )

    print("-" * 100)
    print(f"""
[읽는 법]
  기대승률  최근 득실점을 피타고리안 공식(지수 1.83)에 넣은 '원래 나왔어야 할' 승률
  괴리율    기대승률 - 실제승률. +면 운이 없었던 팀(반등 후보), -면 운이 좋았던 팀(하락 경계)
  구위+     팀 투수진 K-Stuff+ (kbostuff.app, 투구수 가중평균, 100=리그평균)
  타선+     팀 타선 순수 wRC+ (파크팩터·비거리 보정, 100=리그평균)
  모멘텀    {config.momentum_formula()} — 클수록 향후 방향성이 밝음
            (가중치 근거: 2021~2025 백테스트. `main.py --skill-backtest`)

[예측 지평 주의]
  이 판정은 '다음 경기'가 아니라 '향후 20~30경기 방향성'입니다.
  백테스트상 다음 5경기는 사실상 못 맞추고(R²≈0.01), 중기 지평에서만
  신호가 실재합니다(R² 0.05~0.07). 단기 베팅 도구로 쓰지 마세요.
""")


def save_csv(df: pd.DataFrame) -> Path:
    """리포트를 data/report_YYYY-MM-DD.csv 로 저장하고 경로를 돌려줍니다."""
    Path(config.DATA_DIR).mkdir(exist_ok=True)
    out = Path(config.DATA_DIR) / f"report_{date.today()}.csv"

    # 팀 코드를 한글 팀명으로 바꾼 사본을 저장 (엑셀에서 보기 좋게)
    pretty = df.copy()
    pretty.index = [config.TEAM_NAMES.get(t, t) for t in pretty.index]
    pretty.index.name = "팀"
    # 한글 엑셀 호환을 위해 BOM 있는 UTF-8(utf-8-sig)로 저장합니다
    pretty.to_csv(out, encoding="utf-8-sig", float_format="%.4f")
    return out
