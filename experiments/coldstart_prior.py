# -*- coding: utf-8 -*-
"""
콜드 스타트: 시즌 초반 예측에 '전 시즌 prior'를 블렌딩하면 나아지나? — 백테스트
==============================================================================
문제: 개막 첫 경기처럼 당시즌 표본이 0~적을 때, 현재 모델은 팀 지표를 리그평균
  으로 수축시켜 사실상 '코인플립+홈'이 된다. 전 시즌 RS/G·RA/G를 사전확률로 쓰면
  초반 예측이 실제로 개선되나? (FA·트레이드·외국인 교체로 이월이 약해질 수도)

방법: 2022~2026 각 시즌을 날짜순으로 진행하며, 매 경기 예측 P(홈승)을 4가지
  팀강도 추정으로 계산해 실제와 비교(경기수 구간별 Brier/LogLoss/정확도).
  다운스트림(log5식 기대득점→로지스틱)은 앱과 동일 상수로 통일, 오직 '팀 rate
  추정'만 다르게 둔다.
    · lgavg     : 전원 리그평균(홈보정만) — 진짜 콜드스타트 하한
    · asof      : 당시즌 누적 RS/RA, 없으면 리그평균 (=현행 앱 동작 근사)
    · prior     : 전 시즌 RS/RA 그대로
    · blendK    : (전시즌rate×K + 당시즌합)/(K+당시즌경기) — 초반=prior, 후반=현재
  파라미터(WIN_SCALE·SHRINK·HOME_BOOST)는 앱 값 고정 → 피팅 없음(모든 시즌 OOS).
"""
import sys
import math
from collections import defaultdict

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import naver_games  # noqa: E402

SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]   # 2022~2026 검증(각 prior=직전)
SHRINK = 0.85
HOME_BOOST = 1.035
WIN_SCALE = 6.5
KS = [20, 40]                 # 블렌딩 pseudo-games 후보
BUCKETS = [(0, 10), (10, 20), (20, 30), (30, 50), (0, 30)]   # 팀 소화경기 구간


def season_games(season):
    g = naver_games.filter_official_teams(
        naver_games.filter_regular_season(naver_games.fetch_season_games(season)))
    done = [x for x in g if x.get("statusCode") in ("RESULT", "ENDED")
            and not x.get("cancel") and x.get("homeTeamScore") is not None
            and x.get("awayTeamScore") is not None]
    done.sort(key=lambda x: (x.get("gameDate", ""), x.get("gameId", "")))
    return done


def season_rates(done):
    """시즌 전체 팀 RS/G·RA/G와 리그 RS/G."""
    rs = defaultdict(float); ra = defaultdict(float); gp = defaultdict(int)
    for g in done:
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
        rs[h] += hs; ra[h] += as_; gp[h] += 1
        rs[a] += as_; ra[a] += hs; gp[a] += 1
    rate = {t: (rs[t] / gp[t], ra[t] / gp[t]) for t in gp}
    lg = sum(rs.values()) / sum(gp.values())
    return rate, lg


def _idx(v, base):
    return 1 + (v / base - 1) * SHRINK


def p_home(rsH, raH, rsA, raA, lg):
    oH, oA = _idx(rsH, lg), _idx(rsA, lg)
    dH, dA = _idx(raH, lg), _idx(raA, lg)      # 실점력(높을수록 잘 내줌)
    erH = lg * oH * dA * HOME_BOOST
    erA = lg * oA * dH / HOME_BOOST
    return 1 / (1 + math.exp(-(erH - erA) / WIN_SCALE))


def estimate(model, team, cur, prior, lg):
    """(rs_est, ra_est) 반환. cur[team]=(RS합,RA합,GP), prior[team]=(rs/g,ra/g)."""
    RS, RA, GP = cur.get(team, (0.0, 0.0, 0))
    if model == "lgavg":
        return lg, lg
    if model == "asof":
        return (RS / GP, RA / GP) if GP else (lg, lg)
    if model == "prior":
        return prior.get(team, (lg, lg))
    if model.startswith("blend"):
        K = int(model[5:])
        prs, pra = prior.get(team, (lg, lg))
        return ((prs * K + RS) / (K + GP), (pra * K + RA) / (K + GP))
    raise ValueError(model)


def run():
    models = ["lgavg", "asof", "prior"] + [f"blend{k}" for k in KS]
    # bucket -> model -> [(p,y)]
    coll = {b: {m: [] for m in models} for b in BUCKETS}
    rates_by_season = {s: season_rates(season_games(s)) for s in SEASONS}

    for si in range(1, len(SEASONS)):
        season = SEASONS[si]
        prior_rate, prior_lg = rates_by_season[SEASONS[si - 1]]
        lg = prior_lg                       # 개막 전 알 수 있는 리그 런환경(직전시즌)
        done = season_games(season)
        cur = defaultdict(lambda: [0.0, 0.0, 0])   # team -> [RS,RA,GP]
        for g in done:
            h, a = g["homeTeamCode"], g["awayTeamCode"]
            hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
            gpH, gpA = cur[h][2], cur[a][2]
            mgp = max(gpH, gpA)
            if hs != as_:                   # 무승부 제외
                y = 1 if hs > as_ else 0
                curd = {t: tuple(v) for t, v in cur.items()}
                for m in models:
                    rsH, raH = estimate(m, h, curd, prior_rate, lg)
                    rsA, raA = estimate(m, a, curd, prior_rate, lg)
                    p = p_home(rsH, raH, rsA, raA, lg)
                    for b in BUCKETS:
                        if b[0] <= mgp < b[1]:
                            coll[b][m].append((p, y))
            # 상태 업데이트(예측 후)
            cur[h][0] += hs; cur[h][1] += as_; cur[h][2] += 1
            cur[a][0] += as_; cur[a][1] += hs; cur[a][2] += 1

    def brier(pairs):
        return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else float("nan")

    def logloss(pairs):
        s = 0.0
        for p, y in pairs:
            p = min(max(p, 1e-6), 1 - 1e-6)
            s += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        return s / len(pairs) if pairs else float("nan")

    def acc(pairs):
        return sum((p >= .5) == (y == 1) for p, y in pairs) / len(pairs) if pairs else float("nan")

    print("\n" + "=" * 78)
    print("  콜드스타트 백테스트 — 팀 소화경기 구간별 Brier↓ (2022~2026, OOS)")
    print("=" * 78)
    hdr = "  {:<9}".format("구간") + "".join(f"{m:>10}" for m in models) + f"{'n':>7}"
    print(hdr)
    for b in BUCKETS:
        n = len(coll[b][models[0]])
        line = "  {:<9}".format(f"{b[0]}-{b[1]}경기") + "".join(f"{brier(coll[b][m]):>10.4f}" for m in models) + f"{n:>7}"
        print(line)

    print("\n  [0-30경기 구간] LogLoss↓ / 정확도↑")
    for m in models:
        pr = coll[(0, 30)][m]
        print(f"    {m:<9} LogLoss {logloss(pr):.4f} · 정확도 {acc(pr):.1%}")

    print("\n※ 리그평균 상수 WIN_SCALE·SHRINK·HOME_BOOST는 앱 값 고정(피팅 없음).")
    print("  blendK가 asof·lgavg보다 초반 구간 Brier가 낮으면 → 전 시즌 prior 블렌딩이")
    print("  실제로 초반 예측을 개선. 후반 구간에서 asof에 수렴하면 이상적.")


if __name__ == "__main__":
    run()
