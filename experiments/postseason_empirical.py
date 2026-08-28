# -*- coding: utf-8 -*-
"""
가을야구(top5) 확률: 경험적(역사 기반) 모델 vs 몬테카를로 — 백테스트
====================================================================
질문: 시즌 도중 'as-of 상황'만으로 가을야구 진출확률을, MC 시뮬레이션 없이
  과거 데이터로 직접 학습해 맞출 수 있나? MC보다 낫거나 최소한 맞먹나?

방법(정직하게):
  · 과거 완결 시즌(2021~2025)에서 여러 진행시점(40·50·60·70·80%)의 as-of
    팀 상태 → 특징(현재승률·피타고리안·5위와 게임차·잔여경기)과 실제 top5 라벨.
  · 경험적: 로지스틱 회귀(L2). **리브-원-시즌-아웃**(4시즌 학습→나머지 1시즌 검증)
    으로 완전 out-of-sample.
  · MC: 같은 as-of 시점에서 standings_sim.simulate(pyth)의 p_playoff.
  · 동일 (시즌·시점·팀) 지점에서 Brier/LogLoss/정확도로 정면 비교.
    기준선: 상수 0.5, 현재순위 하드(≤5→1).

표본이 5시즌이라 얇다 — '대체'가 아니라 '맞먹나/교차검증되나'를 본다.
"""
import sys
import math
from collections import defaultdict

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import numpy as np  # noqa: E402
import naver_games  # noqa: E402
import standings_sim as ss  # noqa: E402

SEASONS = [2021, 2022, 2023, 2024, 2025]   # 완결 시즌만(2026 진행중 제외)
FRACS = [0.40, 0.50, 0.60, 0.70, 0.80]
TOTAL = ss.TOTAL_GAMES
CUT = ss.PLAYOFF_CUT
L2 = 1.0
MC_SIMS = 4000


def season_full_log(season):
    games = naver_games.filter_official_teams(
        naver_games.filter_regular_season(naver_games.fetch_season_games(season)))
    return games, naver_games.build_team_game_log(games)


def gb_from_5th(rec):
    """각 팀의 5위(진출선)와의 게임차. +면 뒤처짐, -면 앞섬."""
    diff = (rec["w"] - rec["l"])
    fifth = diff.sort_values(ascending=False).iloc[CUT - 1]   # 5위 팀의 승-패
    return (fifth - diff) / 2.0


def build_dataset():
    """리브-원-시즌-아웃용 as-of 표본 + 동일지점 MC p_playoff."""
    logs = {s: season_full_log(s) for s in SEASONS}
    actual_top5 = {}
    for s, (_, full) in logs.items():
        rec_full = ss.current_records(full)
        ar = rec_full["cur_wpct"].rank(ascending=False, method="first").astype(int)
        actual_top5[s] = set(ar[ar <= CUT].index)

    rows = []
    for s, (_, full) in logs.items():
        for frac in FRACS:
            n = round(TOTAL * frac)
            partial = ss._cut_first(full, n)
            rec = ss.current_records(partial)
            gb = gb_from_5th(rec)
            teams = list(rec.index)
            matchups = ss.remaining_from_log(partial, teams)
            sim = ss.simulate(rec, matchups, strength_col="pyth",
                              n_sims=MC_SIMS, home_adv=0.0)
            for t in teams:
                played = int(rec.loc[t, "played"])
                rem = TOTAL - played
                rows.append({
                    "season": s, "frac": frac, "team": t,
                    "cur_wpct": float(rec.loc[t, "cur_wpct"]),
                    "pyth": float(rec.loc[t, "pyth"]),
                    "gb5": float(gb.loc[t]),
                    "rem": rem,
                    "gb5_x_rem": float(gb.loc[t]) * rem / TOTAL,
                    "mc": float(sim.loc[t, "p_playoff"]),
                    "y": 1 if t in actual_top5[s] else 0,
                })
    return rows


FEATS = ["cur_wpct", "pyth", "gb5", "rem", "gb5_x_rem"]


def _fit_logistic(X, y, iters=4000, lr=0.3, l2=L2):
    n, d = X.shape
    w = np.zeros(d); b = 0.0
    for _ in range(iters):
        z = X @ w + b
        p = 1 / (1 + np.exp(-z))
        gw = X.T @ (p - y) / n + l2 * w / n
        gb = float((p - y).mean())
        w -= lr * gw; b -= lr * gb
    return w, b


def loso_empirical(rows):
    """리브-원-시즌-아웃: 학습 시즌으로 표준화·피팅 → 검증 시즌 예측."""
    preds = {}
    for hold in SEASONS:
        tr = [r for r in rows if r["season"] != hold]
        te = [r for r in rows if r["season"] == hold]
        Xtr = np.array([[r[f] for f in FEATS] for r in tr], float)
        ytr = np.array([r["y"] for r in tr], float)
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
        w, b = _fit_logistic((Xtr - mu) / sd, ytr)
        for r in te:
            x = (np.array([r[f] for f in FEATS], float) - mu) / sd
            preds[(r["season"], r["frac"], r["team"])] = 1 / (1 + math.exp(-(x @ w + b)))
    return preds


def metrics(pairs):
    """pairs: [(p, y)] → Brier, LogLoss, Acc(0.5)."""
    br = ll = acc = 0.0
    for p, y in pairs:
        p = min(max(p, 1e-6), 1 - 1e-6)
        br += (p - y) ** 2
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        acc += int((p >= 0.5) == (y == 1))
    n = len(pairs)
    return br / n, ll / n, acc / n


def calib_table(pairs, bins=5):
    buckets = defaultdict(list)
    for p, y in pairs:
        b = min(bins - 1, int(p * bins))
        buckets[b].append((p, y))
    out = []
    for b in range(bins):
        g = buckets.get(b, [])
        if g:
            out.append((f"{b/bins:.1f}-{(b+1)/bins:.1f}", len(g),
                        np.mean([p for p, _ in g]), np.mean([y for _, y in g])))
    return out


def main():
    print("데이터 구축(2021~2025 × 40·50·60·70·80% 시점, MC 동시 계산)…")
    rows = build_dataset()
    emp = loso_empirical(rows)

    emp_pairs, mc_pairs, base_pairs, rankhard_pairs = [], [], [], []
    # 현재순위 하드 기준선: as-of 순위 ≤5 → 1
    for r in rows:
        key = (r["season"], r["frac"], r["team"])
        emp_pairs.append((emp[key], r["y"]))
        mc_pairs.append((r["mc"], r["y"]))
        base_pairs.append((0.5, r["y"]))
    # rank-hard: 시즌·시점별 cur_wpct 순위
    by_sf = defaultdict(list)
    for r in rows:
        by_sf[(r["season"], r["frac"])].append(r)
    for grp in by_sf.values():
        order = sorted(grp, key=lambda r: -r["cur_wpct"])
        for i, r in enumerate(order):
            rankhard_pairs.append((1.0 if i < CUT else 0.0, r["y"]))

    print("\n" + "=" * 72)
    print(f"  가을야구(top5) 확률 백테스트 — 리브-원-시즌-아웃, n={len(rows)} 지점")
    print("=" * 72)
    print(f"\n  {'방법':<22}{'Brier↓':>10}{'LogLoss↓':>11}{'정확도↑':>10}")
    for name, pairs in [("경험적 로지스틱(LOSO)", emp_pairs),
                        ("몬테카를로(pyth)", mc_pairs),
                        ("현재순위 하드(≤5)", rankhard_pairs),
                        ("상수 0.5", base_pairs)]:
        br, ll, acc = metrics(pairs)
        print(f"  {name:<22}{br:>10.4f}{ll:>11.4f}{acc:>9.1%}")

    # 진행시점별 정면 비교(경험적 vs MC)
    print(f"\n  [진행시점별 Brier] 경험적 vs MC")
    print(f"  {'시점':>6}{'경험적':>10}{'MC':>10}{'승자':>8}")
    for frac in FRACS:
        ep = [(emp[(r['season'], r['frac'], r['team'])], r['y']) for r in rows if r['frac'] == frac]
        mp = [(r['mc'], r['y']) for r in rows if r['frac'] == frac]
        be = metrics(ep)[0]; bm = metrics(mp)[0]
        win = "경험적" if be < bm else "MC"
        print(f"  {int(frac*100):>5}%{be:>10.4f}{bm:>10.4f}{win:>8}")

    print(f"\n  [경험적 모델 캘리브레이션]  구간 | n | 평균예측 | 실제")
    for lab, n, mp, my in calib_table(emp_pairs):
        print(f"    {lab:>9} | {n:>3} | {mp:>6.2f} | {my:>5.2f}")

    print(f"\n  [MC 캘리브레이션]  구간 | n | 평균예측 | 실제")
    for lab, n, mp, my in calib_table(mc_pairs):
        print(f"    {lab:>9} | {n:>3} | {mp:>6.2f} | {my:>5.2f}")

    print("\n※ 표본 5시즌. Brier·LogLoss 낮을수록 좋음. 경험적이 MC와 맞먹거나\n"
          "  더 좋고 캘리브레이션이 무너지지 않으면 → 교차검증용 토글로 채택 가치.\n"
          "  반대면 MC만 유지(억지로 넣지 않는다).")


if __name__ == "__main__":
    main()
