# -*- coding: utf-8 -*-
"""
backtest.py — 피타고리안 예측력 백테스트 & 캘리브레이션
========================================================

[핵심 질문]
  "최근 N경기 피타고리안 기대승률(득실점 기반)이, 최근 실제 승률보다
   '다음 N경기'를 더 잘 예측하는가?" — 세이버메트릭스의 고전 명제를
  KBO 2026 실데이터로 직접 검증하고, 최적 지수·윈도우를 찾습니다.

[왜 이 백테스트만 정직한가]
  kbostuff의 K-Stuff+·wRC+는 '현재 시즌 누적값'이라 과거 특정 시점의
  스냅샷이 없습니다. 그래서 그 지표들의 예측력은 백테스트할 수 없습니다.
  반면 경기 결과(득점·실점·승패)는 날짜가 찍혀 있어 완벽한
  point-in-time 백테스트가 가능합니다. 모멘텀 공식의 지배 항(가중치 0.5)인
  '괴리율'이 바로 이 피타고리안에서 나오므로, 이 검증이 모델의 뼈대를
  직접 시험하는 셈입니다.

[방법]
  각 팀의 시즌을 경기 순서대로 훑으며, 시점 t에서:
    - past  = 직전 W경기의 (실제승률, 피타고리안 기대승률)
    - future= 이후 W경기의 실제승률   ← 예측 대상 (미래, 유출 없음)
  모든 (팀, t) 표본을 모아 상관계수를 비교합니다:
    r_actual   = corr(과거 실제승률,   미래 실제승률)
    r_expected = corr(과거 기대승률,   미래 실제승률)
  r_expected > r_actual 이면 '피타고리안이 미래를 더 잘 안다'가 성립.

[캘리브레이션]
  지수(1.5~2.2)와 윈도우(5~20)를 격자 탐색해
  r_expected가 최대가 되는 조합을 추천합니다.
"""

import numpy as np
import pandas as pd


def _pythag(rs, ra, exp):
    """피타고리안 기대승률. 득점·실점이 모두 0이면 0.5로 처리."""
    denom = rs**exp + ra**exp
    return np.where(denom == 0, 0.5, rs**exp / np.where(denom == 0, 1, denom))


def build_samples(team_log: pd.DataFrame, window: int, exp: float) -> pd.DataFrame:
    """
    (팀, 시점) 표본을 만듭니다. 각 표본은 과거 W경기 요약과
    '미래 W경기 실제승률'을 함께 가집니다. (미래가 W경기 안 되면 제외)
    """
    rows = []
    for team, g in team_log.groupby("team"):
        g = g.sort_values("date").reset_index(drop=True)
        win = g["result"].map({"W": 1.0, "D": 0.5, "L": 0.0}).to_numpy()
        rf = g["runs_for"].to_numpy()
        ra = g["runs_against"].to_numpy()
        n = len(g)

        # 과거 W경기가 다 차고, 미래 W경기도 다 차는 시점만 사용
        for t in range(window, n - window + 1):
            past_rs = rf[t - window:t].sum()
            past_ra = ra[t - window:t].sum()
            past_actual = win[t - window:t].mean()
            past_expected = float(_pythag(
                np.array([past_rs]), np.array([past_ra]), exp)[0])
            future_actual = win[t:t + window].mean()
            rows.append({
                "team": team,
                "past_actual": past_actual,
                "past_expected": past_expected,
                "future_actual": future_actual,
            })
    return pd.DataFrame(rows)


def evaluate(team_log: pd.DataFrame, window: int, exp: float) -> dict:
    """한 (윈도우, 지수) 조합의 예측력을 상관계수로 평가."""
    s = build_samples(team_log, window, exp)
    if len(s) < 10:
        return {"n": len(s), "r_actual": np.nan, "r_expected": np.nan}
    r_actual = s["past_actual"].corr(s["future_actual"])
    r_expected = s["past_expected"].corr(s["future_actual"])
    # 절대 예측오차(MAE)도 같이 — 상관 외 실용 지표
    mae_actual = (s["past_actual"] - s["future_actual"]).abs().mean()
    mae_expected = (s["past_expected"] - s["future_actual"]).abs().mean()
    return {
        "n": len(s),
        "r_actual": r_actual, "r_expected": r_expected,
        "lift": r_expected - r_actual,
        "mae_actual": mae_actual, "mae_expected": mae_expected,
    }


def calibrate(team_log: pd.DataFrame,
              windows=(5, 7, 10, 12, 15, 20),
              exps=(1.5, 1.7, 1.83, 2.0, 2.2)) -> pd.DataFrame:
    """
    윈도우×지수 격자를 모두 평가한 표를 반환합니다.
    r_expected(기대승률의 미래 예측 상관)가 높을수록 좋은 조합.
    """
    out = []
    for w in windows:
        for e in exps:
            res = evaluate(team_log, w, e)
            out.append({"window": w, "exp": e, **res})
    return pd.DataFrame(out)


def print_report(team_log: pd.DataFrame) -> pd.DataFrame:
    """콘솔용 백테스트 리포트를 출력하고 격자 결과를 반환합니다."""
    grid = calibrate(team_log)

    print("\n" + "=" * 78)
    print("  피타고리안 예측력 백테스트 — KBO 2026 (point-in-time)")
    print("=" * 78)

    # 표준 설정(윈도우 10, 지수 1.83)의 핵심 결과
    base = evaluate(team_log, 10, 1.83)
    print(f"\n[표준 설정: 최근 10경기 / 지수 1.83]  표본 {base['n']}개")
    print(f"  과거 '실제승률' → 미래 상관 r = {base['r_actual']:.3f}  (MAE {base['mae_actual']:.3f})")
    print(f"  과거 '기대승률' → 미래 상관 r = {base['r_expected']:.3f}  (MAE {base['mae_expected']:.3f})")
    lift = base["r_expected"] - base["r_actual"]
    verdict = "✅ 피타고리안이 더 정확 (모델 뼈대 유효)" if lift > 0 \
        else "⚠️ 이 시즌엔 실제승률이 더 정확"
    print(f"  예측력 향상(lift) = {lift:+.3f}  → {verdict}")

    # 최적 조합 (r_expected 최대)
    best = grid.dropna(subset=["r_expected"]).sort_values(
        "r_expected", ascending=False).iloc[0]
    print(f"\n[격자 탐색 최적] 윈도우 {int(best['window'])}경기 / 지수 {best['exp']} "
          f"→ r_expected {best['r_expected']:.3f} (표본 {int(best['n'])})")

    print("\n[윈도우별 최적 지수 요약]")
    print(f"  {'윈도우':>5} {'최적지수':>7} {'r_기대':>7} {'r_실제':>7} {'향상':>7}")
    for w, sub in grid.groupby("window"):
        b = sub.dropna(subset=["r_expected"]).sort_values(
            "r_expected", ascending=False)
        if len(b) == 0:
            continue
        b = b.iloc[0]
        print(f"  {int(w):>5} {b['exp']:>7} {b['r_expected']:>7.3f} "
              f"{b['r_actual']:>7.3f} {b['lift']:>+7.3f}")

    print("""
[읽는 법]
  r_기대 > r_실제 이면, 득실점 기반 기대승률이 최근 성적표보다
  다음 경기들을 더 잘 예측한다는 뜻 = '괴리율로 반등/하락을 잡는다'는
  모델 전제가 데이터로 지지됨. lift(향상)가 클수록 그 효과가 큼.
  단, 한 시즌 표본이라 절대값보다 '방향과 부호'를 보세요.
""")
    return grid
