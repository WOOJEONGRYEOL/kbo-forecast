# -*- coding: utf-8 -*-
"""
업셋 신호 실증: 피타고리안 운(괴리율) · 득점 분산(boom-bust)
============================================================
질문1: 강팀이 '득실차보다 순위가 높은(운 좋은)' 상태면, 약팀이 실제로 더 자주 이기나?
질문2: 약팀의 득점 변동성이 크면(boom-bust), 실제로 업셋을 더 하나? (변동성=업셋의 본질)

설계: 각 경기에서
  · 경기 시점의 누적(그 경기 전까지) 피타고리안 기대승률·실제승률 → 강팀/약팀·괴리율 산출
  · 강팀 = 경기 전 피타고리안 기대승률 높은 팀. 약팀 = 반대.
  · underdog_won = 약팀이 이겼나
  · 괴리율(강팀) = 강팀 실제승률 − 강팀 피타고리안(양수=운 좋음)
  · 분산 우위 = 약팀 시즌 득점 표준편차 − 강팀 시즌 득점 표준편차
  실력 교란은 '전력차(피타고리안 차)'를 각 구간에서 함께 보고, 평균 통제로 확인.
"""
import sys
from collections import defaultdict

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import naver_games  # noqa: E402
import config  # noqa: E402

EXP = getattr(config, "PYTHAG_EXPONENT", 1.83)
SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]
MIN_PRIOR = 20   # 경기 전 누적 표본 최소


def pyth(rs, ra):
    if rs <= 0 and ra <= 0:
        return 0.5
    return rs ** EXP / (rs ** EXP + ra ** EXP)


def wr_se(wins, n):
    if n == 0:
        return (float("nan"), float("nan"))
    p = wins / n
    return (p, (p * (1 - p) / n) ** 0.5)


games_rec = []   # 경기: {gap, fav_luck, var_edge, ud_won, strength_gap}
for season in SEASONS:
    try:
        games = naver_games.filter_official_teams(
            naver_games.filter_regular_season(naver_games.fetch_season_games(season)))
    except Exception:
        continue
    done = [g for g in games if g.get("statusCode") in ("RESULT", "ENDED")
            and not g.get("cancel") and g.get("homeTeamScore") is not None
            and g.get("awayTeamScore") is not None]
    done.sort(key=lambda x: (x.get("gameDate", ""), x.get("gameDateTime", "")))
    # 시즌 득점 표준편차(팀별, 전 경기)
    runs = defaultdict(list)
    for g in done:
        runs[g["homeTeamCode"]].append(g["homeTeamScore"])
        runs[g["awayTeamCode"]].append(g["awayTeamScore"])

    def std(xs):
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    team_std = {t: std(v) for t, v in runs.items()}
    team_mean = {t: (sum(v) / len(v) if v else 0) for t, v in runs.items()}

    # 경기 시점 누적(그 경기 전까지)
    cum = defaultdict(lambda: {"rs": 0, "ra": 0, "w": 0, "l": 0, "n": 0})
    for g in done:
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
        ch, ca = cum[h], cum[a]
        if ch["n"] >= MIN_PRIOR and ca["n"] >= MIN_PRIOR:
            def stat(c):
                py = pyth(c["rs"], c["ra"]); ac = c["w"] / (c["w"] + c["l"]) if (c["w"] + c["l"]) else 0.5
                return py, ac
            hpy, hac = stat(ch); apy, aac = stat(ca)
            if hpy >= apy:
                fav, ud, fpy, upy, f_luck = h, a, hpy, apy, hac - hpy
            else:
                fav, ud, fpy, upy, f_luck = a, h, apy, hpy, aac - apy
            ud_won = 1 if ((h == ud and hs > as_) or (a == ud and as_ > hs)) else 0 if hs != as_ else None
            if ud_won is not None:
                games_rec.append({
                    "gap": fpy - upy, "fav_luck": f_luck,
                    "var_edge": team_std.get(ud, 0) - team_std.get(fav, 0),
                    "ud_cv_edge": (team_std.get(ud, 0) / team_mean.get(ud, 1) if team_mean.get(ud) else 0)
                                  - (team_std.get(fav, 0) / team_mean.get(fav, 1) if team_mean.get(fav) else 0),
                    "ud_won": ud_won})
        # 누적 갱신(경기 후)
        ch["rs"] += hs; ch["ra"] += as_; ch["n"] += 1
        ca["rs"] += as_; ca["ra"] += hs; ca["n"] += 1
        if hs > as_:
            ch["w"] += 1; ca["l"] += 1
        elif as_ > hs:
            ca["w"] += 1; ch["l"] += 1

R = games_rec
base_p, base_se = wr_se(sum(r["ud_won"] for r in R), len(R))
print(f"=== 표본: {len(R)} 경기 (약팀 관점, 전력 확정 후) ===")
print(f"기저 약팀 승률: {base_p*100:.1f}% ±{base_se*100:.1f}  (강팀이 {(1-base_p)*100:.0f}% 이김)\n")


def report(title, keyfn, order, note=""):
    g = defaultdict(list)
    for r in R:
        g[keyfn(r)].append(r)
    print(f"── {title} ──  {note}")
    print(f"{'구간':<18}{'경기':>6}{'약팀 승률':>12}{'전력차(평균)':>14}")
    for k in order:
        rl = g.get(k, [])
        if not rl:
            continue
        p, se = wr_se(sum(r["ud_won"] for r in rl), len(rl))
        gap = sum(r["gap"] for r in rl) / len(rl)
        z = (p - base_p) / (base_se) if base_se else 0
        star = "  ***" if abs(z) >= 2.58 else "  **" if abs(z) >= 1.96 else "  ·" if abs(z) >= 1.64 else ""
        print(f"{k:<18}{len(rl):>6}{p*100:>9.1f}% ±{se*100:.1f}{gap:>12.3f}{star}")
    print()


# 질문1: 강팀 괴리율(운) → 약팀 승률
report("강팀 피타고리안 운(괴리율)별 약팀 승률", lambda r:
       "강팀 운없음(<−.03)" if r["fav_luck"] < -0.03 else
       "강팀 중립(±.03)" if r["fav_luck"] <= 0.03 else
       "강팀 운좋음(>.03)",
       ["강팀 운없음(<−.03)", "강팀 중립(±.03)", "강팀 운좋음(>.03)"],
       "가설: 운 좋은 강팀일수록 약팀 승률↑")

# 질문2: 약팀 득점 분산 우위 → 약팀 승률
report("약팀 득점 분산 우위(약팀 std − 강팀 std)별 약팀 승률", lambda r:
       "분산 열세(<−.3)" if r["var_edge"] < -0.3 else
       "분산 비슷(±.3)" if r["var_edge"] <= 0.3 else
       "분산 우위(>.3)",
       ["분산 열세(<−.3)", "분산 비슷(±.3)", "분산 우위(>.3)"],
       "가설: 변동성 큰 약팀일수록 업셋↑ (변동성=업셋의 본질)")

# 참고: 전력차 통제 위해 '접전(gap<.06)' 경기만 재검
close = [r for r in R if r["gap"] < 0.06]
if close:
    bp, bse = wr_se(sum(r["ud_won"] for r in close), len(close))
    print(f"[접전만(전력차<.06), n={len(close)}] 기저 약팀 승률 {bp*100:.1f}%")
    for lab, f in [("강팀 운좋음(>.03)", lambda r: r["fav_luck"] > 0.03),
                   ("약팀 분산 우위(>.3)", lambda r: r["var_edge"] > 0.3)]:
        sub = [r for r in close if f(r)]
        if sub:
            p, se = wr_se(sum(r["ud_won"] for r in sub), len(sub))
            print(f"  {lab}: n={len(sub)} 약팀 승률 {p*100:.1f}% ±{se*100:.1f} (기저 대비 {(p-bp)*100:+.1f}%p)")

print("\n※ ***=99% · **=95% · ·=90% (기저 약팀 승률과 다름).")
