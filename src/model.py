# -*- coding: utf-8 -*-
"""
model.py — 단기 전력 예측 모델
================================

Gemini 대화에서 설계한 3단계 모델을 그대로 구현합니다.
(원본 템플릿 코드의 SyntaxError — f-string 안의 'Baltic' 오타 — 는
 이 구현에서 수정되어 있습니다)

  1단계  최근 N경기 득점/실점 집계 (rolling window)
  2단계  피타고리안 기대승률 계산  →  "이 득실점이면 원래 몇 승이 정상인가?"
  3단계  괴리율(기대 - 실제) 스크리닝 →  운이 나빴던 팀(반등 후보) /
                                         운이 좋았던 팀(하락 경계) 탐지

여기에 kbostuff.app의 시즌 스킬 지표(K-Stuff+, 타선+)를 z-score로
표준화해 결합한 '종합 모멘텀 지수'까지 산출합니다.
"""

import numpy as np
import pandas as pd

import config


def rolling_pythagorean(team_log: pd.DataFrame,
                        window: int = config.ROLLING_WINDOW) -> pd.DataFrame:
    """
    팀별 '최근 window경기' 요약표를 만듭니다.

    입력  : build_team_game_log()가 만든 팀 관점 경기 로그
    반환  : 팀당 1행 DataFrame
      recent_w / recent_l / recent_d  최근 N경기 승/패/무
      actual_wpct   실제 승률   (무승부는 0.5승으로 계산 — 관례)
      expected_wpct 피타고리안 기대승률
      gap           괴리율 = 기대 - 실제
                    (+) 경기력 대비 운이 없었다 → 반등 가능성
                    (-) 경기력 대비 운이 좋았다 → 하락 가능성
    """
    results = []

    for team, g in team_log.groupby("team"):
        g = g.sort_values("date")

        # window=None 이면 시즌 전체, 아니면 최근 window경기.
        # (시즌 초반이라 경기 수가 window보다 적으면 있는 만큼만 사용)
        recent = g if window is None else g.tail(window)
        n = len(recent)

        rs = recent["runs_for"].sum()        # Runs Scored (득점)
        ra = recent["runs_against"].sum()    # Runs Allowed (실점)
        w = (recent["result"] == "W").sum()
        l = (recent["result"] == "L").sum()
        d = (recent["result"] == "D").sum()

        # ── 실제 승률 ──
        # KBO 공식 승률 계산은 무승부를 제외하지만, 여기서는
        # 기대승률과 척도를 맞추기 위해 무승부 = 0.5승으로 넣습니다.
        actual = (w + 0.5 * d) / n

        # ── 피타고리안 기대승률 ──
        # RS^1.83 / (RS^1.83 + RA^1.83)
        # "득실점 마진이 곧 실력"이라는 세이버메트릭스의 대원칙.
        e = config.PYTHAG_EXPONENT
        if rs == 0 and ra == 0:      # 극단 상황 방어 (둘 다 0이면 0.5)
            expected = 0.5
        else:
            expected = rs**e / (rs**e + ra**e)

        results.append({
            "team": team,
            "recent_games": n,
            "recent_w": w, "recent_l": l, "recent_d": d,
            "recent_rs": rs, "recent_ra": ra,
            "actual_wpct": actual,
            "expected_wpct": expected,
            "gap": expected - actual,
        })

    return pd.DataFrame(results).set_index("team")


def rolling_trend(team_log: pd.DataFrame,
                  window: int = config.ROLLING_WINDOW) -> dict:
    """
    대시보드의 '시즌 흐름' 차트용 데이터를 만듭니다.

    팀마다 시즌 개막부터 오늘까지, 매 경기 시점의
    "직전 N경기 기대승률 / 실제승률"을 시계열로 계산합니다.
    → 두 선이 벌어지는 구간 = 운이 성적을 왜곡하던 구간입니다.

    반환 형식 (JSON으로 바로 직렬화 가능):
      { "LG": { "dates": [...], "expected": [...], "actual": [...] }, ... }
    """
    e = config.PYTHAG_EXPONENT
    out: dict = {}

    for team, g in team_log.groupby("team"):
        g = g.sort_values("date").reset_index(drop=True)

        # 승=1, 무=0.5, 패=0 으로 수치화 (기대승률과 척도 통일)
        win_value = g["result"].map({"W": 1.0, "D": 0.5, "L": 0.0})

        # 직전 window경기의 득점/실점 합 (표본이 다 차기 전에는 NaN).
        # window=None 이면 개막부터 누적(expanding) — '시즌 전체 기준' 추이.
        if window is None:
            rs = g["runs_for"].expanding(min_periods=5).sum()
            ra = g["runs_against"].expanding(min_periods=5).sum()
            actual = win_value.expanding(min_periods=5).mean()
        else:
            rs = g["runs_for"].rolling(window, min_periods=window).sum()
            ra = g["runs_against"].rolling(window, min_periods=window).sum()
            actual = win_value.rolling(window, min_periods=window).mean()

        expected = rs**e / (rs**e + ra**e)

        ok = expected.notna()  # 윈도우가 다 찬 구간만 사용
        out[team] = {
            "dates": g.loc[ok, "date"].tolist(),
            "expected": [round(float(v), 3) for v in expected[ok]],
            "actual": [round(float(v), 3) for v in actual[ok]],
        }

    return out


def season_summary(team_log: pd.DataFrame) -> pd.DataFrame:
    """시즌 전체 성적 요약 (리포트에 참고용으로 함께 표시)."""
    rows = []
    for team, g in team_log.groupby("team"):
        w = (g["result"] == "W").sum()
        l = (g["result"] == "L").sum()
        d = (g["result"] == "D").sum()
        rows.append({
            "team": team,
            "season_g": len(g),
            "season_w": w, "season_l": l, "season_d": d,
            # KBO 공식 방식: 무승부 제외 승률
            "season_wpct": w / (w + l) if (w + l) > 0 else 0.0,
        })
    return pd.DataFrame(rows).set_index("team")


def _zscore(s: pd.Series) -> pd.Series:
    """표준화(z-score): 평균 0, 표준편차 1로 변환.
    서로 단위가 다른 지표(승률 괴리 vs Stuff+ 점수)를
    같은 저울 위에 올리기 위한 표준 기법입니다."""
    std = s.std(ddof=0)
    if std == 0 or np.isnan(std):
        return s * 0.0
    return (s - s.mean()) / std


def combine(pythag: pd.DataFrame,
            season: pd.DataFrame,
            pitching: pd.Series,
            batting: pd.DataFrame,
            fcb: pd.Series | None = None,
            pitching_rot: pd.Series | None = None) -> pd.DataFrame:
    """
    모든 재료를 한 표로 합치고 '종합 모멘텀 지수'를 계산합니다.

    종합 모멘텀 = 0.5 × z(괴리율)          ← 단기 운 요소 (반등/하락 에너지)
               + 0.25 × z(팀 K-Stuff+)     ← 투수진의 순수 구위
               + 0.25 × z(팀 타선 wRC+pure) ← 타선의 순수 생산력

    가중치 근거: 단기 예측에서는 '최근 폼과 운의 되돌림(regression)'이
    가장 강한 신호라서 절반을 주고, 나머지 절반을 투타 실력에
    균등 배분했습니다. 실측 검증 후 조절해 보세요. (README 참고)
    """
    df = pythag.join(season)
    df["team_stuff_plus"] = pitching
    df["bat_overall_plus"] = batting["bat_overall_plus"]
    df["bat_wrc_pure"] = batting["bat_wrc_pure"]

    # 팀 FCB 승리기여 합 (kbostuff 고유 지표).
    # ⚠️ 클러치는 잘 지속되지 않아 '미래 예측'에는 넣지 않습니다.
    #   대신 '지금까지 승부처에 강했나'를 보여주는 설명형 컬럼으로만 표시합니다.
    if fcb is not None:
        df["team_fcb"] = fcb
    else:
        df["team_fcb"] = float("nan")

    # 전체 투수진 기준 모멘텀 (기본)
    df["momentum"] = (
        0.50 * _zscore(df["gap"])
        + 0.25 * _zscore(df["team_stuff_plus"])
        + 0.25 * _zscore(df["bat_wrc_pure"])
    )

    # 선발 로테이션 기준 모멘텀 (토글용).
    # 투수력 항만 '선발 로테이션 K-Stuff+'로 갈아끼웁니다.
    # (다음 시리즈에 나올 투수는 불펜이 아니라 선발이므로 매치업에 더 적합)
    if pitching_rot is not None:
        df["team_stuff_rot"] = pitching_rot
        df["momentum_rot"] = (
            0.50 * _zscore(df["gap"])
            + 0.25 * _zscore(df["team_stuff_rot"])
            + 0.25 * _zscore(df["bat_wrc_pure"])
        )
    else:
        df["team_stuff_rot"] = df["team_stuff_plus"]
        df["momentum_rot"] = df["momentum"]

    # ── 진단 코멘트 ──
    # 괴리율 ±0.05 = 10경기 기준 승수 0.5개 차이.
    # 이보다 크면 "성적과 경기력이 따로 노는 팀"으로 봅니다.
    def diagnose(row) -> str:
        if row["gap"] > config.GAP_THRESHOLD:
            return "📈 반등 후보 — 경기력 대비 운이 없었음 (매수)"
        if row["gap"] < -config.GAP_THRESHOLD:
            return "📉 하락 경계 — 경기력 대비 승리를 과하게 챙김 (조심)"
        return "➖ 적정 — 성적이 경기력과 일치"

    df["diagnosis"] = df.apply(diagnose, axis=1)

    # 종합 모멘텀이 높은 순서(단기 미래가 밝은 순서)로 정렬
    return df.sort_values("momentum", ascending=False)
