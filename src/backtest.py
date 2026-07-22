# -*- coding: utf-8 -*-
"""
backtest.py — 피타고리안 예측력 백테스트 & 캘리브레이션
========================================================

[핵심 질문]
  "최근 N경기 피타고리안 기대승률(득실점 기반)이, 최근 실제 승률보다
   '다음 N경기'를 더 잘 예측하는가?" — 세이버메트릭스의 고전 명제를
  KBO 2026 실데이터로 직접 검증하고, 최적 지수·윈도우를 찾습니다.

[왜 이 백테스트가 모델의 뼈대인가]
  모멘텀 공식의 지배 항(가중치 0.5)인 '괴리율'이 피타고리안에서 나오므로,
  이 검증이 모델의 중심을 직접 시험하는 셈입니다.

[구위 항도 이제 검증됩니다 — 2026-07 갱신]
  예전에는 "kbostuff의 K-Stuff+는 시즌 누적값이라 과거 시점 스냅샷이
  없고, 따라서 구위 항의 예측력은 백테스트할 수 없다"고 적어두었습니다.
  그 전제는 이제 사실이 아닙니다. pitching_metrics_v2_game_log가
  경기 단위 K-Stuff+를 2021년까지 제공하므로, 구위 항도 완전한
  point-in-time 검증이 가능합니다. → print_skill_report() 참고

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


def build_skill_samples(team_log: pd.DataFrame,
                        by_game: pd.DataFrame,
                        window: int,
                        exp: float = 1.83,
                        stuff_mode: str = "season",
                        future_window: int | None = None) -> pd.DataFrame:
    """
    (팀, 시점) 표본에 '그 시점까지의 팀 구위'를 붙입니다.

    시점 t에서 (미래 정보 유출 없이):
      past_actual = 직전 window경기 실제승률
      past_gap    = 직전 window경기 피타고리안 기대승률 − 실제승률
      past_stuff  = 그 시점까지의 팀 K-Stuff+ (투구수 가중, 리그 재센터링)
      future_wpct = 이후 future_window경기 실제승률    ← 예측 대상

    future_window=None 이면 window와 같은 값(기존 동작).
    입력 창(window)과 예측 지평(future_window)을 분리해 "얼마나 앞을
    내다보느냐"에 따른 예측력을 잴 수 있습니다. (calibrate_horizon 참고)

    stuff_mode:
      "season"  개막~직전까지 누적 (기본)
      "recent"  직전 window경기만

    [왜 기본이 season인가 — 검증 결과]
      직관적으로는 괴리율과 시간축을 맞춘 "recent"가 옳아 보이지만,
      2021~2025 백테스트에서 recent가 모든 윈도우에서 일관되게
      더 나빴습니다 (r 차이 −0.035 ~ −0.048).
        윈도우 10: season +0.089 vs recent +0.048
        윈도우 20: season +0.146 vs recent +0.107
      K-Stuff+는 시즌간 stickiness 0.80의 '안정적 스킬' 지표라,
      추정에 표본이 많을수록 정확합니다. 최근 10경기 구위는
      진짜 실력이 아니라 노이즈를 측정하게 됩니다.
      → "스킬 지표가 시즌 누적이라 최근 폼 반영이 늦다"는 것은
        약점이 아니라 오히려 예측에 유리한 성질이었습니다.

    ⚠️ 구위는 '경기 t 이전'까지만 씁니다(shift(1)). 경기 t 당일의
      구위를 넣으면 미래 정보가 새어 들어갑니다.
    """
    fw = future_window if future_window is not None else window

    # (팀, 날짜) 단위 구위 — 더블헤더는 그날치를 합칩니다
    stuff = (by_game.groupby(["team", "game_date"], as_index=False)
             .agg(stuff_wsum=("stuff_wsum", "sum"), pitches=("pitches", "sum"))
             .rename(columns={"game_date": "date"}))

    out = []
    for team, g in team_log.groupby("team"):
        g = (g.sort_values("date")
             .merge(stuff[stuff["team"] == team].drop(columns="team"),
                    on="date", how="left")
             .reset_index(drop=True))

        # 게임로그가 없는 경기는 0으로 둬서 rolling 합에 기여만 안 하게 합니다
        g[["stuff_wsum", "pitches"]] = g[["stuff_wsum", "pitches"]].fillna(0.0)

        win = g["result"].map({"W": 1.0, "D": 0.5, "L": 0.0})

        past_rs = g["runs_for"].rolling(window).sum().shift(1)
        past_ra = g["runs_against"].rolling(window).sum().shift(1)
        past_actual = win.rolling(window).mean().shift(1)

        if stuff_mode == "recent":
            past_sw = g["stuff_wsum"].rolling(window).sum().shift(1)
            past_p = g["pitches"].rolling(window).sum().shift(1)
        else:  # "season" — 개막부터 직전 경기까지 누적
            past_sw = g["stuff_wsum"].expanding(min_periods=window).sum().shift(1)
            past_p = g["pitches"].expanding(min_periods=window).sum().shift(1)

        # 시점 t의 '이후 fw경기' 실제승률 (예측 지평)
        future = win.rolling(fw).mean().shift(-(fw - 1))

        past_expected = _pythag(past_rs.to_numpy(), past_ra.to_numpy(), exp)

        out.append(pd.DataFrame({
            "team": team,
            "date": g["date"],
            "past_actual": past_actual,
            "past_expected": past_expected,
            "past_gap": past_expected - past_actual,
            "past_stuff": past_sw / past_p.where(past_p > 0),
            "future_wpct": future,
        }))

    s = pd.concat(out, ignore_index=True)
    return s.dropna(subset=["past_gap", "past_stuff", "future_wpct"])


def _ols_r2(X: pd.DataFrame, y: pd.Series) -> tuple:
    """최소자승 회귀 → (계수배열, R²). scipy 없이 numpy만 씁니다."""
    A = np.column_stack([np.ones(len(X))] + [X[c].to_numpy() for c in X.columns])
    beta, *_ = np.linalg.lstsq(A, y.to_numpy(), rcond=None)
    resid = y.to_numpy() - A @ beta
    ss_tot = ((y.to_numpy() - y.mean()) ** 2).sum()
    return beta, 1 - (resid ** 2).sum() / ss_tot if ss_tot else float("nan")


def _partial_corr(x: pd.Series, y: pd.Series, z: pd.Series) -> float:
    """z를 통제한 x와 y의 편상관 (각각 z에 회귀시킨 잔차끼리의 상관)."""
    rx = x - np.polyval(np.polyfit(z, x, 1), z)
    ry = y - np.polyval(np.polyfit(z, y, 1), z)
    return float(np.corrcoef(rx, ry)[0, 1])


def incremental_value(samples: pd.DataFrame) -> dict:
    """
    '과거 성적을 이미 안다고 할 때, 각 항이 정보를 더 주는가'를 잽니다.

    [왜 단순 상관으로는 안 되나 — 중요]
      괴리율 = 기대승률 − 실제승률 이므로 과거 실제승률과 구조적으로
      음의 상관을 가집니다. 그런데 과거 성적 자체가 미래를 양으로
      예측하므로, 괴리율의 '단순' 상관은 필연적으로 음수가 나옵니다.
      이걸 근거로 "괴리율은 쓸모없다"고 결론내면 틀립니다.

      괴리율의 진짜 주장은 "과거 성적을 감안하고도 반등한다"이므로,
      과거 승률을 통제한 편상관 / 증분 R²로 봐야 공정합니다.
    """
    y = samples["future_wpct"]
    _, r2_base = _ols_r2(samples[["past_actual"]], y)
    _, r2_gap = _ols_r2(samples[["past_actual", "past_gap"]], y)
    _, r2_stuff = _ols_r2(samples[["past_actual", "past_stuff"]], y)
    _, r2_both = _ols_r2(
        samples[["past_actual", "past_gap", "past_stuff"]], y)
    return {
        "n": len(samples),
        "r2_past": r2_base,
        "d_gap": r2_gap - r2_base,
        "d_stuff": r2_stuff - r2_base,
        "d_both": r2_both - r2_base,
        "pc_gap": _partial_corr(samples["past_gap"], y, samples["past_actual"]),
        "pc_stuff": _partial_corr(samples["past_stuff"], y,
                                  samples["past_actual"]),
    }


def _z(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else s * 0.0


def evaluate_momentum(samples: pd.DataFrame,
                      w_stuff_grid=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5)) -> pd.DataFrame:
    """
    '괴리율 + 구위' 결합 가중치를 바꿔가며 미래 예측 상관을 잽니다.

    모델의 모멘텀은 z-score를 섞으므로 여기서도 동일하게 표준화한 뒤
    (1-w)·z(괴리율) + w·z(구위) 조합의 예측력을 봅니다.
    w=0이 현재 뼈대(괴리율만), w가 클수록 구위 비중이 큽니다.

    ※ 실제 모멘텀에는 타선 항도 있지만, 타자 쪽은 경기 단위 구위에
      대응하는 스킬 로그가 없어(fcb_game_log는 클러치 기여라 성격이 다름)
      여기서는 투수 항만 검증합니다.
    """
    zg = _z(samples["past_gap"])
    zs = _z(samples["past_stuff"])
    fut = samples["future_wpct"]

    rows = []
    for w in w_stuff_grid:
        combo = (1 - w) * zg + w * zs
        rows.append({"w_stuff": w, "r": combo.corr(fut)})
    return pd.DataFrame(rows)


def print_skill_report(team_log: pd.DataFrame,
                       by_game: pd.DataFrame,
                       windows=(10, 15, 20),
                       label: str = "") -> pd.DataFrame:
    """
    구위 항이 미래 예측에 실제로 기여하는지 검증하는 리포트.

    [읽는 법]
      r(괴리율만) 대비 r(괴리율+구위)가 높아지면 구위 항이 값어치를 합니다.
      최적 w_stuff가 0에 붙으면 "구위는 단기 예측에 도움이 안 된다"는 뜻이고,
      그렇다면 모멘텀에서 구위 가중치를 낮추는 것이 정직한 대응입니다.
    """
    print("\n" + "=" * 78)
    print(f"  구위 항 예측력 백테스트 {label}".rstrip())
    print("=" * 78)

    all_rows = []
    for w in windows:
        s = build_skill_samples(team_log, by_game, w)
        if len(s) < 30:
            print(f"\n[윈도우 {w}] 표본 부족({len(s)}) — 건너뜀")
            continue

        inc = incremental_value(s)
        print(f"\n[윈도우 {w}경기]  표본 {inc['n']}개")
        print("  단순 상관(참고용):"
              f"  과거승률 {s['past_actual'].corr(s['future_wpct']):+.3f}"
              f" | 괴리율 {s['past_gap'].corr(s['future_wpct']):+.3f}"
              f" | 구위 {s['past_stuff'].corr(s['future_wpct']):+.3f}")
        print(f"  과거승률만으로 설명되는 미래 R² = {inc['r2_past']:.4f}")
        print(f"    + 괴리율 → 증분 R² {inc['d_gap']:+.4f}"
              f"  (편상관 {inc['pc_gap']:+.3f})")
        print(f"    + 구위   → 증분 R² {inc['d_stuff']:+.4f}"
              f"  (편상관 {inc['pc_stuff']:+.3f})")

        # 두 항의 상대적 가치 = 편상관 비율 (음수는 0으로 눌러 안전하게)
        pg, ps = max(inc["pc_gap"], 0.0), max(inc["pc_stuff"], 0.0)
        if pg + ps > 0:
            print(f"    → 증거가 시사하는 괴리율:구위 비중 "
                  f"= {pg / (pg + ps):.2f} : {ps / (pg + ps):.2f}")

        all_rows.append({"window": w, **inc})

    if all_rows:
        print("""
[읽는 법]
  '증분 R²'는 과거 성적을 이미 안 상태에서 그 항이 추가로 설명하는 몫입니다.
  단순 상관이 아니라 이 값으로 판단해야 괴리율에 공정합니다.
  절대값이 작은 것(R² 0.01~0.05)은 모델 잘못이 아니라 단기 야구가
  원래 그만큼 예측 불가능하다는 뜻입니다. 중요한 건 항들 사이의 '상대 크기'입니다.
""")
    return pd.DataFrame(all_rows)


def calibrate_horizon(team_log: pd.DataFrame,
                      by_game: pd.DataFrame,
                      past_windows=(10, 15, 20),
                      horizons=(5, 10, 20, 30, 40)) -> pd.DataFrame:
    """
    입력 창(past_window) × 예측 지평(horizon)의 2차원 격자에서
    모멘텀 결합(과거승률+괴리율+구위)이 미래 승률을 얼마나 설명하는지(R²)
    측정합니다.

    핵심 질문: "얼마나 앞을 내다보면 얼마나 맞는가?"
    """
    rows = []
    for pw in past_windows:
        for h in horizons:
            s = build_skill_samples(team_log, by_game, pw, future_window=h)
            if len(s) < 50:
                rows.append({"past": pw, "horizon": h, "n": len(s),
                             "r2": float("nan")})
                continue
            _, r2 = _ols_r2(s[["past_actual", "past_gap", "past_stuff"]],
                            s["future_wpct"])
            rows.append({"past": pw, "horizon": h, "n": len(s), "r2": r2})
    return pd.DataFrame(rows)


def print_horizon_report(team_log: pd.DataFrame,
                         by_game: pd.DataFrame) -> pd.DataFrame:
    """예측 지평별 예측력 표를 출력합니다."""
    grid = calibrate_horizon(team_log, by_game)

    print("\n" + "=" * 78)
    print("  예측 지평 캘리브레이션 — '얼마나 앞을 보면 얼마나 맞는가'")
    print("=" * 78)

    horizons = sorted(grid["horizon"].unique())
    header = "  입력\\지평 " + "".join(f"{h:>8}경기" for h in horizons)
    print("\n" + header)
    for pw, sub in grid.groupby("past"):
        sub = sub.set_index("horizon")
        cells = "".join(f"{sub.loc[h, 'r2']:>12.4f}" for h in horizons)
        print(f"  {pw:>6}경기{cells}")

    print("""
[읽는 법]
  값 = 미래 승률을 설명하는 R². 세로 = 최근 몇 경기로 입력, 가로 = 몇 경기 앞 예측.
  '다음 5경기'는 사실상 못 맞춥니다(R²≈0.01). 지평을 20~30경기로 늘리면
  R²가 3~5배가 됩니다 — 단기는 노이즈, 중기 방향성은 실재한다는 뜻입니다.
  단, 지평을 40경기(≈6주)까지 늘리면 '곧 반등'이 아니라 '원래 좋은 팀이냐'로
  수렴해 실행 가치가 떨어집니다. 정확도와 실용성의 절충점은 지평 20~30경기입니다.
""")
    return grid


def _auc(score: np.ndarray, label: np.ndarray) -> float:
    """
    ROC AUC (scipy 없이 rank 방식). label은 0/1.
    0.5 = 동전던지기, 1.0 = 완벽 분류. 이진 결과 예측력의 표준 척도입니다.
    """
    r = pd.Series(score).rank().to_numpy()
    n1 = label.sum()
    n0 = len(label) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return (r[label == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def build_matchup_samples(games: list,
                          box: "pd.DataFrame",
                          game_log: pd.DataFrame,
                          form_window: int = 10,
                          recenter: bool = True) -> pd.DataFrame:
    """
    경기 단위 표본을 만듭니다. 각 경기에 대해 (미래 유출 없이):
      home_stuff / away_stuff : 그 경기 '이전까지'의 선발 누적 K-Stuff+
                                (투구수 가중, 리그 재센터링)
      home_form / away_form   : 그 경기 이전 최근 form_window경기 팀 승률
      home_win                : 홈팀 승리 여부 (무승부는 제외)

    선발 = 박스스코어의 (경기, 팀) 첫 등판 투수.
    game_id = 'YYYYMMDD + 원정 + 홈 + ...' 이므로 홈/원정을 코드로 가릅니다.
    """
    # ── 1. 완료 경기: gameId, date, home/away code, 홈 승패 ──
    rec = []
    for g in games:
        if g.get("statusCode") != "RESULT" or g.get("cancel"):
            continue
        hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
        if hs is None or as_ is None or hs == as_:  # 무승부·미확정 제외
            continue
        rec.append({"game_id": g["gameId"], "date": g["gameDate"],
                    "home": g["homeTeamCode"], "away": g["awayTeamCode"],
                    "home_win": int(hs > as_)})
    res = pd.DataFrame(rec)
    if res.empty:
        return res

    # ── 2. 선발의 시점별 누적 구위 ──
    gl = game_log.dropna(subset=["k_stuff_v2", "n_pitches"]).copy()
    gl["pitcher_pcode"] = gl["pitcher_pcode"].astype(str)
    gl["game_id"] = gl["game_id"].astype(str)
    if recenter:
        from kbostuff_client import daily_league_stuff
        base = daily_league_stuff(gl)
        gl["k_stuff_v2"] = gl["k_stuff_v2"] - gl["game_date"].map(base) + 100.0

    # 각 등판을 시간순으로, '그 경기 이전까지' 투구수 가중 누적 구위
    gl = gl.sort_values(["pitcher_pcode", "game_date"])
    gl["cum_sw"] = (gl.groupby("pitcher_pcode")
                    .apply(lambda d: (d["k_stuff_v2"] * d["n_pitches"]).cumsum()
                           .shift(1), include_groups=False).reset_index(level=0, drop=True))
    gl["cum_p"] = (gl.groupby("pitcher_pcode")["n_pitches"]
                   .apply(lambda s: s.cumsum().shift(1))
                   .reset_index(level=0, drop=True))
    gl["prior_stuff"] = gl["cum_sw"] / gl["cum_p"].where(gl["cum_p"] > 0)
    # (경기, 투수) → 그 경기 직전까지 구위
    prior = gl[["game_id", "pitcher_pcode", "prior_stuff"]]

    # 박스스코어 첫 투수 = 그 경기 선발
    starters = (box.sort_index()
                .drop_duplicates(["game_id", "team"], keep="first")
                [["game_id", "team", "pcode"]]
                .astype({"game_id": str, "pcode": str}))
    starters = starters.merge(
        prior, left_on=["game_id", "pcode"],
        right_on=["game_id", "pitcher_pcode"], how="left")

    # game_id×team → 구위 를 홈/원정으로 나눠 붙입니다
    s_home = starters.rename(columns={"team": "home", "prior_stuff": "home_stuff"})
    s_away = starters.rename(columns={"team": "away", "prior_stuff": "away_stuff"})
    res = res.merge(s_home[["game_id", "home", "home_stuff"]],
                    on=["game_id", "home"], how="left")
    res = res.merge(s_away[["game_id", "away", "away_stuff"]],
                    on=["game_id", "away"], how="left")

    # ── 3. 팀 최근 폼 (그 경기 이전 form_window경기 승률) ──
    long = pd.concat([
        res[["date", "home", "home_win"]].rename(
            columns={"home": "team"}).assign(w=res["home_win"]),
        res[["date", "away", "home_win"]].rename(
            columns={"away": "team"}).assign(w=1 - res["home_win"]),
    ], ignore_index=True).sort_values("date")
    long["form"] = (long.groupby("team")["w"]
                    .apply(lambda s: s.rolling(form_window).mean().shift(1))
                    .reset_index(level=0, drop=True))
    # date+team 으로 되돌려 붙이기 (같은 날 같은 팀은 유일하다고 가정)
    form_map = long.dropna(subset=["form"])
    res = res.merge(
        form_map[["date", "team", "form"]].rename(
            columns={"team": "home", "form": "home_form"}),
        on=["date", "home"], how="left")
    res = res.merge(
        form_map[["date", "team", "form"]].rename(
            columns={"team": "away", "form": "away_form"}),
        on=["date", "away"], how="left")

    return res


def print_matchup_report(games: list, box: "pd.DataFrame",
                         game_log: pd.DataFrame,
                         label: str = "") -> pd.DataFrame:
    """
    선발 매치업(구위차)이 팀 최근 폼보다 '개별 경기 결과'를 잘 맞추는지 비교.

    두 예측자를 같은 경기 표본에서 AUC로 비교합니다:
      · 선발 구위차 = home_stuff − away_stuff
      · 팀 폼차     = home_form  − away_form
    (홈 어드밴티지는 양쪽에 공통 상수라 AUC 비교에는 영향 없음)
    """
    s = build_matchup_samples(games, box, game_log)
    print("\n" + "=" * 78)
    print(f"  선발 매치업 vs 팀 폼 — 개별 경기 예측력 {label}".rstrip())
    print("=" * 78)

    both = s.dropna(subset=["home_stuff", "away_stuff",
                            "home_form", "away_form", "home_win"])
    if len(both) < 50:
        print(f"\n  표본 부족({len(both)}) — 건너뜀")
        return s

    y = both["home_win"].to_numpy()
    auc_stuff = _auc((both["home_stuff"] - both["away_stuff"]).to_numpy(), y)
    auc_form = _auc((both["home_form"] - both["away_form"]).to_numpy(), y)
    # 둘을 합친 로지스틱 대용: 표준화 후 단순 합
    z_stuff = _z(both["home_stuff"] - both["away_stuff"])
    z_form = _z(both["home_form"] - both["away_form"])
    auc_both = _auc((z_stuff + z_form).to_numpy(), y)
    base_home = y.mean()

    print(f"\n  표본 {len(both)}경기 (무승부 제외)  |  홈 승률 {base_home:.3f}")
    print(f"  선발 구위차   → AUC {auc_stuff:.3f}")
    print(f"  팀 최근 폼차  → AUC {auc_form:.3f}")
    print(f"  둘 다 결합    → AUC {auc_both:.3f}")
    print(f"""
[판정]
  AUC 0.5 = 동전던지기. 매치업(구위차)이 팀 폼보다 높으면 선발 정보가
  개별 경기 예측에 값어치를 한다는 뜻입니다.
  → 구위차 {auc_stuff:.3f} vs 폼 {auc_form:.3f}: {"매치업 우위 ✅ 당일 카드 구현 가치 있음"
        if auc_stuff > auc_form + 0.005 else
        "폼과 비슷하거나 열위 ⚠️ 당일 카드는 조회용 이상 의미 약함"}
  ※ 절대 AUC가 0.5~0.6대인 건 정상입니다 — 단일 경기는 원래 거의 코인플립입니다.
""")
    return s


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
