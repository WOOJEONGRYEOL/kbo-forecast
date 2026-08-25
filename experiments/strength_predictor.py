# -*- coding: utf-8 -*-
"""
팀 전력 지표 비교: 단일경기 승자 예측력 (out-of-sample)
=======================================================
질문: 팀 전력을 어떤 지표로 잡아야 '다음 경기 승자'를 잘 맞히나?
  A. 피타고리안(득실차 기반)   B. 실제 승률(운·클러치 포함)   C. 블렌드

설계: 경기 시점 누적(그 경기 전까지) 지표로 홈-원정 전력차 x = S_home − S_away.
  학습(2021~2024)에서 로지스틱 P(홈승)=σ(b0+b1·x) 피팅 → 검증(2025~2026)에서
  정확도·Brier·LogLoss 측정. 무승부 제외. as-of라 미래 누수 없음.
"""
import sys
import math
from collections import defaultdict

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import naver_games  # noqa: E402
import config  # noqa: E402

EXP = getattr(config, "PYTHAG_EXPONENT", 1.83)
TRAIN = [2021, 2022, 2023, 2024]
TEST = [2025, 2026]
MIN_PRIOR = 20


def pyth(rs, ra):
    if rs <= 0 and ra <= 0:
        return 0.5
    return rs ** EXP / (rs ** EXP + ra ** EXP)


def build(seasons):
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
        cum = defaultdict(lambda: {"rs": 0, "ra": 0, "w": 0, "l": 0, "n": 0})
        for g in done:
            h, a = g["homeTeamCode"], g["awayTeamCode"]
            hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
            ch, ca = cum[h], cum[a]
            if ch["n"] >= MIN_PRIOR and ca["n"] >= MIN_PRIOR and hs != as_:
                def stat(c):
                    return (pyth(c["rs"], c["ra"]),
                            c["w"] / (c["w"] + c["l"]) if (c["w"] + c["l"]) else 0.5)
                hpy, hac = stat(ch); apy, aac = stat(ca)
                recs.append({"py": hpy - apy, "ac": hac - aac, "y": 1 if hs > as_ else 0})
            ch["rs"] += hs; ch["ra"] += as_; ch["n"] += 1
            ca["rs"] += as_; ca["ra"] += hs; ca["n"] += 1
            if hs > as_:
                ch["w"] += 1; ca["l"] += 1
            elif as_ > hs:
                ca["w"] += 1; ch["l"] += 1
    return recs


def fit_logistic(xs, ys, iters=4000, lr=0.3):
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
    z = [(x - m) / sd for x in xs]
    b0 = b1 = 0.0
    n = len(z)
    for _ in range(iters):
        g0 = g1 = 0.0
        for x, t in zip(z, ys):
            p = 1 / (1 + math.exp(-(b0 + b1 * x)))
            g0 += p - t; g1 += (p - t) * x
        b0 -= lr * g0 / n; b1 -= lr * g1 / n
    return (b0, b1, m, sd)


def predict(model, x):
    b0, b1, m, sd = model
    return 1 / (1 + math.exp(-(b0 + b1 * ((x - m) / sd))))


def evaluate(model, recs, feat):
    acc = brier = ll = 0.0
    for r in recs:
        p = predict(model, feat(r))
        y = r["y"]
        acc += 1 if (p >= 0.5) == (y == 1) else 0
        brier += (p - y) ** 2
        p = min(max(p, 1e-6), 1 - 1e-6)
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    n = len(recs)
    return acc / n * 100, brier / n, ll / n


tr = build(TRAIN); te = build(TEST)
print(f"=== 학습 {TRAIN} n={len(tr)} · 검증 {TEST} n={len(te)} (무승부 제외) ===")
home_wr = sum(r["y"] for r in te) / len(te) * 100
print(f"검증셋 홈 승률(기저): {home_wr:.1f}%  → '무조건 홈' 정확도 {max(home_wr, 100-home_wr):.1f}%\n")

feats = {
    "A. 피타고리안(득실차)": lambda r: r["py"],
    "B. 실제 승률": lambda r: r["ac"],
    "C. 블렌드 0.25실제": lambda r: 0.75 * r["py"] + 0.25 * r["ac"],
    "C. 블렌드 0.50실제": lambda r: 0.50 * r["py"] + 0.50 * r["ac"],
    "C. 블렌드 0.75실제": lambda r: 0.25 * r["py"] + 0.75 * r["ac"],
}
print(f"{'지표':<20}{'정확도':>9}{'Brier↓':>10}{'LogLoss↓':>11}")
for name, f in feats.items():
    model = fit_logistic([f(r) for r in tr], [r["y"] for r in tr])
    acc, br, ll = evaluate(model, te, f)
    print(f"{name:<20}{acc:>8.1f}%{br:>10.4f}{ll:>11.4f}")

print("\n※ 정확도↑·Brier↓·LogLoss↓ 가 좋음. 단일경기 천장 ~56% 근처.")
print("  실제/블렌드가 피타고리안보다 낫다면 → 본체에 실제 성적 반영 가치 있음.")
