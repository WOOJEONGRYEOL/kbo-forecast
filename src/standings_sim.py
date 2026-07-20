# -*- coding: utf-8 -*-
"""
standings_sim.py — 시즌 최종 순위 몬테카를로 시뮬레이터
=========================================================

[핵심 아이디어]
  "다음 10경기 누가 반등하나"는 노이즈라 못 맞춰도(R²≈0.01),
  "시즌 최종 순위"는 꽤 맞춥니다. 역설적이지만 이유는 단순합니다:
  시즌의 60%+가 이미 확정됐고, 남은 40%만 불확실하기 때문입니다.
  순위는 그 확정분 위에서 결정되므로 예측 지평이 길수록(=진행률이
  높을수록) 오히려 안정적입니다. (backtest.py 지평 표의 연장선)

[잔여 대진표가 없어도 되는 이유]
  KBO는 팀당 각 상대와 정확히 16경기(9팀×16=144)를 치릅니다.
  그래서 지금까지 팀쌍별 몇 번 붙었는지만 세면, 남은 매치업의
  '상대와 횟수'가 규칙으로 완전히 복원됩니다. 순위 확률에 필요한 건
  '남은 상대가 누구냐'지 '몇 월 며칠이냐'가 아닙니다. 게다가 팀쌍
  16경기 = 홈8+원정8이라 홈/원정 배분까지 역산됩니다.
  → 일정(날짜·구장)이 나중에 공개돼도 순위 확률은 안 바뀝니다.
    일정은 '날짜축 진행 시각화'에만 필요합니다.

[방법]
  1. 팀 강도 = 피타고리안 기대승률 (득실점 기반, 실제승률보다 미래를
     잘 맞춘다는 게 이 프로젝트 백테스트의 결론)
  2. 남은 각 경기 승률 = log5(홈강도, 원정강도) + 홈 어드밴티지
  3. 남은 경기를 베르누이로 N회 시뮬 → 팀별 최종 승수 분포 → 순위 분포
  4. 1위 확률 / 가을야구(top5) 확률 / 순위 90% 구간을 산출
"""

import numpy as np
import pandas as pd

import config

PER_PAIR = 16          # KBO: 팀당 각 상대와 16경기
TOTAL_GAMES = 144      # 팀당 정규시즌 경기 수
PLAYOFF_CUT = 5        # 가을야구 진출선 (5위까지)

# KBO 홈 승률은 대략 0.53~0.54. 승률에 더하는 홈 어드밴티지 근사치.
# (log5 결과에 더하고 [0,1]로 클립합니다)
HOME_ADVANTAGE = 0.035


def _done_games(games: list) -> list:
    """점수가 확정된 정규시즌 경기만."""
    return [g for g in games
            if g.get("statusCode") == "RESULT" and not g.get("cancel")
            and g.get("homeTeamScore") is not None
            and g.get("awayTeamScore") is not None]


def current_records(team_log: pd.DataFrame) -> pd.DataFrame:
    """팀별 현재 승/패/무 + 득실점 → 피타고리안 강도."""
    e = config.PYTHAG_EXPONENT
    rows = []
    for team, g in team_log.groupby("team"):
        w = int((g["result"] == "W").sum())
        l = int((g["result"] == "L").sum())
        d = int((g["result"] == "D").sum())
        rs = float(g["runs_for"].sum())
        ra = float(g["runs_against"].sum())
        pyth = 0.5 if rs == 0 and ra == 0 else rs**e / (rs**e + ra**e)
        rows.append({"team": team, "w": w, "l": l, "d": d,
                     "played": len(g), "rs": rs, "ra": ra,
                     "cur_wpct": w / (w + l) if (w + l) else 0.0,
                     "pyth": pyth})
    return pd.DataFrame(rows).set_index("team")


def remaining_matchups(games: list, teams: list) -> list:
    """
    잔여 매치업을 16경기 규칙으로 복원합니다.

    반환: [(home_team, away_team), ...] — 남은 경기 1건당 1튜플.
      홈/원정은 각 팀쌍이 홈8·원정8이 되도록 역산합니다.
    """
    from collections import Counter
    home_played = Counter()   # (home, away) -> 횟수
    for g in _done_games(games):
        home_played[(g["homeTeamCode"], g["awayTeamCode"])] += 1

    half = PER_PAIR // 2      # 8 (팀쌍당 각 팀 홈경기 수)
    out = []
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            # a가 홈인 남은 경기 = 8 - (a홈 소화), b가 홈인 남은 경기 = 8 - (b홈 소화)
            a_home_left = max(0, half - home_played[(a, b)])
            b_home_left = max(0, half - home_played[(b, a)])
            out += [(a, b)] * a_home_left
            out += [(b, a)] * b_home_left
    return out


def _log5(pa: np.ndarray, pb: np.ndarray) -> np.ndarray:
    """
    log5: 승률 pa인 팀이 승률 pb인 팀을 이길 확률.
      P = (pa − pa·pb) / (pa + pb − 2·pa·pb)
    Bill James의 표준 공식. 두 팀이 리그 평균 상대라면 각자 승률대로,
    강팀끼리 붙으면 자동으로 승률이 조정됩니다.
    """
    denom = pa + pb - 2 * pa * pb
    return np.where(denom == 0, 0.5, (pa - pa * pb) / np.where(denom == 0, 1, denom))


def remaining_from_log(team_log: pd.DataFrame, teams: list) -> list:
    """
    팀로그(opponent 컬럼)만으로 잔여 매치업을 복원합니다. 홈/원정은 구분하지
    않습니다(백테스트용 — 홈 어드밴티지는 순위 상관에 거의 영향 없음).
    """
    from collections import Counter
    cnt = Counter()
    for _, r in team_log.iterrows():
        cnt[(r["team"], r["opponent"])] += 1
    out = []
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            left = max(0, PER_PAIR - cnt[(a, b)])
            out += [(a, b)] * left
    return out


def simulate(records: pd.DataFrame,
             matchups: list,
             strength_col: str = "pyth",
             n_sims: int = 20000,
             seed: int = 7,
             home_adv: float = HOME_ADVANTAGE) -> pd.DataFrame:
    """
    몬테카를로로 최종 순위 분포를 만듭니다.

    records      : current_records() 결과 (강도 컬럼 포함)
    matchups     : remaining_matchups() 결과
    strength_col : 팀 강도로 쓸 컬럼 ("pyth" 기본, "blend" 등 교체 가능)

    반환: 팀별 예상 최종승률, 1위 확률, 가을야구(top5) 확률,
          순위 중앙값, 순위 5~95% 구간.
    """
    rng = np.random.default_rng(seed)
    teams = list(records.index)
    idx = {t: i for i, t in enumerate(teams)}
    T = len(teams)

    strength = records[strength_col].to_numpy()
    base_w = records["w"].to_numpy(dtype=float)
    base_dec = (records["w"] + records["l"]).to_numpy(dtype=float)  # 승+패

    # 남은 경기 → (홈idx, 원정idx) 배열
    if matchups:
        hi = np.array([idx[h] for h, _ in matchups])
        ai = np.array([idx[a] for _, a in matchups])
        p_home = np.clip(_log5(strength[hi], strength[ai]) + home_adv,
                         0.01, 0.99)
    else:
        hi = ai = np.array([], dtype=int)
        p_home = np.array([])

    M = len(matchups)
    # 각 시뮬에서 팀별 최종 승수
    final_w = np.tile(base_w, (n_sims, 1)).T            # (T, n_sims)
    final_games = base_dec.copy()                        # 팀별 최종 승+패
    for t in teams:                                      # 남은 경기수 더하기
        final_games[idx[t]] += sum(1 for h, a in matchups if t in (h, a))

    if M:
        # (M, n_sims) 홈승 여부
        home_win = rng.random((M, n_sims)) < p_home[:, None]
        # 팀별 승수 누적
        add_w = np.zeros((T, n_sims))
        np.add.at(add_w, hi, home_win)                   # 홈 이기면 홈팀 +1
        np.add.at(add_w, ai, ~home_win)                  # 홈 지면 원정팀 +1
        final_w += add_w

    wpct = final_w / final_games[:, None]                # (T, n_sims)
    # 순위: 승률 내림차순 (동률은 안정적 처리)
    order = np.argsort(-wpct, axis=0, kind="stable")
    ranks = np.empty_like(order)
    rows = np.arange(T)[:, None]
    np.put_along_axis(ranks, order, np.broadcast_to(rows, order.shape), axis=0)
    ranks = ranks + 1                                    # 1 = 최고 승률

    res = []
    for t in teams:
        r = ranks[idx[t]]
        res.append({
            "team": t,
            "proj_wpct": float(wpct[idx[t]].mean()),
            "p_first": float((r == 1).mean()),
            "p_playoff": float((r <= PLAYOFF_CUT).mean()),
            "rank_median": int(np.median(r)),
            "rank_lo": int(np.percentile(r, 5)),
            "rank_hi": int(np.percentile(r, 95)),
        })
    return (pd.DataFrame(res).set_index("team")
            .sort_values("proj_wpct", ascending=False))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """순위 상관(스피어만) = 두 순위 벡터의 피어슨 상관. scipy 불필요."""
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def _cut_first(team_log: pd.DataFrame, n_games: int) -> pd.DataFrame:
    """각 팀의 날짜순 첫 n_games경기만 남깁니다 (point-in-time 재현)."""
    return (team_log.sort_values("date")
            .groupby("team", group_keys=False).head(n_games))


def backtest_final_rank(season_logs: dict,
                        cutoff_frac: float = 0.61,
                        methods=("cur_wpct", "pyth"),
                        n_sims: int = 5000) -> pd.DataFrame:
    """
    과거 완결 시즌에서 'cutoff_frac 진행 시점'까지의 데이터로 최종 순위를
    시뮬한 뒤, 실제 최종 순위와 비교합니다.

    강도 방식(현재승률 vs 피타고리안)별로:
      spearman  : 예측 순위 ↔ 실제 순위 상관 (1에 가까울수록 정확)
      top5_hit  : 가을야구(5위 안) 5팀 중 몇 팀을 맞췄나
      first_hit : 1위를 맞췄나 (0/1)

    season_logs : {season: 완결된 team_log}
    """
    cutoff_games = round(TOTAL_GAMES * cutoff_frac)
    rows = []
    for season, full in season_logs.items():
        rec_full = current_records(full)
        actual_rank = (rec_full["cur_wpct"].rank(ascending=False, method="first")
                       .astype(int))
        actual_top5 = set(actual_rank[actual_rank <= PLAYOFF_CUT].index)
        actual_first = actual_rank.idxmin()

        partial = _cut_first(full, cutoff_games)
        rec = current_records(partial)
        teams = list(rec.index)
        matchups = remaining_from_log(partial, teams)

        for m in methods:
            sim = simulate(rec, matchups, strength_col=m,
                           n_sims=n_sims, home_adv=0.0)
            pred_rank = pd.Series(range(1, len(sim) + 1), index=sim.index)
            # 공통 팀 정렬로 상관 계산
            common = actual_rank.index
            sp = _spearman(pred_rank.reindex(common).to_numpy(),
                           actual_rank.reindex(common).to_numpy())
            pred_top5 = set(pred_rank[pred_rank <= PLAYOFF_CUT].index)
            rows.append({
                "season": season, "method": m,
                "spearman": sp,
                "top5_hit": len(pred_top5 & actual_top5),
                "first_hit": int(sim.index[0] == actual_first),
            })
    return pd.DataFrame(rows)


def print_backtest(season_logs: dict, cutoff_frac: float = 0.61) -> pd.DataFrame:
    """순위 예측 백테스트 결과를 콘솔에 출력합니다."""
    bt = backtest_final_rank(season_logs, cutoff_frac=cutoff_frac)
    print("\n" + "=" * 78)
    print(f"  최종 순위 예측 백테스트 — {int(cutoff_frac*100)}% 진행 시점 → 최종")
    print("=" * 78)
    print(f"\n  {'강도방식':<10}{'스피어만':>10}{'가을야구적중':>12}{'1위적중':>9}"
          f"   (시즌 {len(season_logs)}개 평균)")
    for m, sub in bt.groupby("method"):
        nm = {"cur_wpct": "현재승률", "pyth": "피타고리안"}.get(m, m)
        print(f"  {nm:<10}{sub['spearman'].mean():>10.3f}"
              f"{sub['top5_hit'].mean():>10.1f}/5{sub['first_hit'].mean():>8.2f}")
    print(f"""
[읽는 법]
  스피어만 1.0 = 최종 순위를 완벽히 맞춤, 0 = 무작위.
  {int(cutoff_frac*100)}% 진행 시점에서도 이미 순위 상관이 높다면, '남은 40%는
  대세를 못 바꾼다'는 뜻입니다 — 최종 순위 예측이 단기 예측보다 쉬운 이유.
  피타고리안이 현재승률보다 높으면, 득실점 기반 강도가 최종 순위를
  더 잘 예측한다는 뜻입니다.
""")
    return bt


def run(games: list, team_log: pd.DataFrame,
        strength_col: str = "pyth", n_sims: int = 20000) -> pd.DataFrame:
    """수집된 경기·팀로그로 최종 순위 시뮬을 돌리고 표를 합쳐 반환합니다."""
    rec = current_records(team_log)
    teams = list(rec.index)
    matchups = remaining_matchups(games, teams)
    sim = simulate(rec, matchups, strength_col=strength_col, n_sims=n_sims)
    out = rec.join(sim)
    out["remaining"] = TOTAL_GAMES - out["played"]
    return out.sort_values("proj_wpct", ascending=False)
