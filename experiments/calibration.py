# -*- coding: utf-8 -*-
"""
승률 계수 캘리브레이션: WIN_SCALE·HOME_BOOST가 정직한가?
=======================================================
우리 모델: 승률 = σ( (기대득점차 M) / WIN_SCALE ),  기대득점은 log5(공격×상대실점)×홈보정.
질문: 현재 WIN_SCALE=5.5, HOME_BOOST=1.035 가 과확신/과소확신인가?

방법: 경기 시점 누적 RS/G·RA/G로 log5 기대득점차 M을 만들고(우리 구조와 동일),
  학습(2021~24)에서 P(홈승)=σ(b0+b1·M) 피팅 → 최적 WIN_SCALE=1/b1, b0=홈 잔여.
  검증(2025~26) Brier로 '우리 값' vs '최적 값' 비교.
"""
import sys
import math
from collections import defaultdict

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import naver_games  # noqa: E402
import config  # noqa: E402

TRAIN = [2021, 2022, 2023, 2024]
TEST = [2025, 2026]
MIN_PRIOR = 20
CUR_WIN_SCALE = getattr(config, "WIN_SCALE", 5.5) if hasattr(config, "WIN_SCALE") else 5.5
CUR_HOME = 1.035


def build(seasons, home_boost):
    """각 경기 as-of log5 기대득점차 M(홈−원정)과 결과."""
    recs = []
    for season in seasons:
        try:
            games = naver_games.filter_official_teams(
                naver_games.filter_regular_season(naver_games.fetch_season_games(season)))
        except Exception:
            continue
        done = [g for g in games if g.get("statusCode") in ("RESULT", "ENDED")
                and not g.get("cancel") and g.get("homeTeamScore") is not None
                and g.get("awayTeamScore") is not None]
        done.sort(key=lambda x: (x.get("gameDate", ""), x.get("gameDateTime", "")))
        lg = (sum(g["homeTeamScore"] + g["awayTeamScore"] for g in done)
              / (2 * len(done))) if done else 4.8
        cum = defaultdict(lambda: {"rs": 0, "ra": 0, "n": 0})
        for g in done:
            h, a = g["homeTeamCode"], g["awayTeamCode"]
            hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
            ch, ca = cum[h], cum[a]
            if ch["n"] >= MIN_PRIOR and ca["n"] >= MIN_PRIOR and hs != as_:
                rsh, rah = ch["rs"] / ch["n"], ch["ra"] / ch["n"]
                rsa, raa = ca["rs"] / ca["n"], ca["ra"] / ca["n"]
                exp_h = lg * (rsh / lg) * (raa / lg) * home_boost
                exp_a = lg * (rsa / lg) * (rah / lg) / home_boost
                recs.append({"M": exp_h - exp_a, "y": 1 if hs > as_ else 0})
            ch["rs"] += hs; ch["ra"] += as_; ch["n"] += 1
            ca["rs"] += as_; ca["ra"] += hs; ca["n"] += 1
    return recs


def fit(recs, iters=6000, lr=0.2):
    xs = [r["M"] for r in recs]; ys = [r["y"] for r in recs]
    b0 = b1 = 0.0; n = len(xs)
    for _ in range(iters):
        g0 = g1 = 0.0
        for x, t in zip(xs, ys):
            p = 1 / (1 + math.exp(-(b0 + b1 * x)))
            g0 += p - t; g1 += (p - t) * x
        b0 -= lr * g0 / n; b1 -= lr * g1 / n
    return b0, b1


def brier_ll(recs, b0, b1):
    br = ll = 0.0
    for r in recs:
        p = 1 / (1 + math.exp(-(b0 + b1 * r["M"])))
        p = min(max(p, 1e-6), 1 - 1e-6)
        br += (p - r["y"]) ** 2; ll += -(r["y"] * math.log(p) + (1 - r["y"]) * math.log(1 - p))
    return br / len(recs), ll / len(recs)


tr = build(TRAIN, CUR_HOME); te = build(TEST, CUR_HOME)
print(f"=== 학습 {TRAIN} n={len(tr)} · 검증 {TEST} n={len(te)} ===")
print(f"검증 홈 승률: {sum(r['y'] for r in te)/len(te)*100:.1f}%\n")

# 1) 최적 피팅
b0, b1 = fit(tr)
opt_scale = 1 / b1 if b1 else float("inf")
# b0=0 근처면 홈보정(1.035)이 이미 M에 반영돼 적정. b0>0이면 홈이 더 세야 함.
implied_home_p = 1 / (1 + math.exp(-b0))
print(f"[학습셋 최적] WIN_SCALE ≈ {opt_scale:.2f}  (우리 값 {CUR_WIN_SCALE})")
print(f"             홈 잔여 b0 = {b0:+.3f} → 기대차 0일 때 홈승 {implied_home_p*100:.1f}%")
print(f"             (b0≈0이면 HOME_BOOST {CUR_HOME}가 적정, b0>0이면 홈을 더 세게)\n")

# 2) 검증셋 Brier: 우리 값(5.5, 홈보정만) vs 최적
b1_cur = 1 / CUR_WIN_SCALE            # 우리 모델: σ(M/5.5), 홈은 M 안에 있음(b0=0)
br_cur, ll_cur = brier_ll(te, 0.0, b1_cur)
br_opt, ll_opt = brier_ll(te, b0, b1)
print(f"{'설정':<22}{'Brier↓':>10}{'LogLoss↓':>11}")
print(f"{'우리(5.5, 홈보정1.035)':<22}{br_cur:>10.4f}{ll_cur:>11.4f}")
print(f"{'최적(피팅)':<22}{br_opt:>10.4f}{ll_opt:>11.4f}")

# 3) WIN_SCALE만 스윕(홈은 우리 것 유지=b0 0)
print("\n[WIN_SCALE 스윕, 홈보정 우리 값 유지]")
best = (None, 9)
for ws in [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0]:
    br, ll = brier_ll(te, 0.0, 1 / ws)
    mark = "  ← 우리" if abs(ws - CUR_WIN_SCALE) < 1e-6 else ""
    if br < best[1]:
        best = (ws, br)
    print(f"  WIN_SCALE {ws:>4}: Brier {br:.4f} · LogLoss {ll:.4f}{mark}")
print(f"  → 검증 Brier 최소: WIN_SCALE {best[0]}")
print("\n※ M은 as-of 시즌 RS/RA 기반 log5(우리 구조 근사). 선발·라인업 미포함이라 우리 실모델보다\n"
      "  기대차가 완만할 수 있음 → 최적 WIN_SCALE은 참고치. 방향성(과확신/과소확신) 판단용.")
