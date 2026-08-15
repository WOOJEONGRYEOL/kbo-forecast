# -*- coding: utf-8 -*-
"""
single_game_prediction.py — KBO 단일 경기 예측 재현 실험
=========================================================

한국체육학회지 64권 2호 논문("한국프로야구 경기결과 예측을 위한 머신러닝 성능
비교…", 2013~2023 15,488경기, 로지스틱 회귀 59.3% 최적)을 우리 데이터로 재현하고
정직한 point-in-time 검증 vs 누수(leakage) 검증을 대조한다.

핵심 질문: 논문의 59.3%는 (a) 진짜 예측력인가, (b) 시즌 집계 스탯을 k-fold에
넣어 생긴 누수 아티팩트인가?

실행: .venv/bin/python experiments/single_game_prediction.py
결과·해석: experiments/README.md
"""

import sys
import warnings
from collections import defaultdict

sys.path.insert(0, "src")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import numpy as np
import naver_games
import kbostuff_client as kc
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

E = 1.83                 # 피타고리안 지수
SEASONS = [2021, 2022, 2023, 2024, 2025]
MIN_PRIOR = 15           # 두 팀 모두 15경기+ 소화 이후만 (피처 안정화)
FORM_W = 10              # 최근 폼 윈도우
EWMA_ALPHA = 0.25        # 최근가중 강도(클수록 최근 편중; 반감기 ~2.4경기)


def _ewma(vals, alpha=EWMA_ALPHA):
    """지수가중이동평균 — 최근 경기(마지막 원소)에 더 큰 가중."""
    if not vals:
        return 0.0
    s = vals[0]
    for x in vals[1:]:
        s = alpha * x + (1 - alpha) * s
    return s


def starter_stuff_by_game(season: int) -> dict:
    """
    game_id → {team: 선발의 season-to-date 재센터 K-Stuff}.
    선발 = 그 경기 팀 최다 투구 투수. 시점별(미래 정보 없음).
    """
    gl = kc.fetch_pitching_game_log(season).dropna(
        subset=["k_stuff_v2", "n_pitches"]).copy()
    gl["pitcher_pcode"] = gl["pitcher_pcode"].astype(str)
    gl["game_id"] = gl["game_id"].astype(str)
    base = kc.daily_league_stuff(gl)                  # 스케일 드리프트 제거
    gl["ks"] = gl["k_stuff_v2"] - gl["game_date"].map(base) + 100.0
    teams = kc.infer_pitcher_teams(gl).set_index("pitcher_pcode")["team"].to_dict()
    gl["team"] = gl["pitcher_pcode"].map(teams)
    gl = gl.dropna(subset=["team"]).sort_values("game_date")
    # '그 경기 이전까지' 투구수 가중 재센터 K-Stuff
    gl["csw"] = gl.groupby("pitcher_pcode").apply(
        lambda d: (d["ks"] * d["n_pitches"]).cumsum().shift(1), include_groups=False
    ).reset_index(level=0, drop=True)
    gl["cp"] = gl.groupby("pitcher_pcode")["n_pitches"].apply(
        lambda s: s.cumsum().shift(1)).reset_index(level=0, drop=True)
    gl["prior"] = gl["csw"] / gl["cp"].where(gl["cp"] > 0)
    out = {}
    for gid, grp in gl.groupby("game_id"):
        d = {}
        for tm, tg in grp.groupby("team"):
            s = tg.loc[tg["n_pitches"].idxmax(), "prior"]
            if not np.isnan(s):
                d[tm] = s
        out[gid] = d
    return out


def build() -> list:
    """각 완료 경기 → point-in-time 피처 + (누수 비교용) 시즌 최종 피타고리안."""
    rows = []
    final_pyth = {}
    for s in SEASONS:
        games = naver_games.filter_official_teams(
            naver_games.filter_regular_season(naver_games.fetch_season_games(s)))
        done = [g for g in games if g.get("statusCode") == "RESULT"
                and not g.get("cancel") and g.get("homeTeamScore") is not None
                and g.get("homeTeamScore") != g.get("awayTeamScore")]
        done.sort(key=lambda g: g["gameDateTime"])

        # 시즌 최종 득실 → 최종 피타고리안 (누수 테스트용, '미래' 정보)
        tot = defaultdict(lambda: [0.0, 0.0])
        for g in done:
            tot[g["homeTeamCode"]][0] += g["homeTeamScore"]
            tot[g["homeTeamCode"]][1] += g["awayTeamScore"]
            tot[g["awayTeamCode"]][0] += g["awayTeamScore"]
            tot[g["awayTeamCode"]][1] += g["homeTeamScore"]
        for t, (rs, ra) in tot.items():
            final_pyth[(s, t)] = rs**E / (rs**E + ra**E) if (rs or ra) else 0.5

        starter = starter_stuff_by_game(s)
        st = {}
        for g in done:
            h, a = g["homeTeamCode"], g["awayTeamCode"]
            hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
            for t in (h, a):
                st.setdefault(t, {"rs": 0.0, "ra": 0.0, "wins": [],
                                  "rs_g": [], "ra_g": []})

            def feat(t):
                d = st[t]
                if len(d["wins"]) < MIN_PRIOR:
                    return None
                pyth = (d["rs"]**E / (d["rs"]**E + d["ra"]**E)
                        if (d["rs"] or d["ra"]) else 0.5)
                n = len(d["wins"])
                sea_rs, sea_ra = d["rs"] / n, d["ra"] / n     # 시즌 평균 득/실
                return {
                    "pyth": pyth,
                    "form": np.mean(d["wins"][-FORM_W:]),
                    # 최근가중(EWMA) 투(득점)·타(실점) — 사용자 제안
                    "ewma_rs": _ewma(d["rs_g"]),
                    "ewma_ra": _ewma(d["ra_g"]),
                    # '핫/콜드' 델타: 최근가중 − 시즌평균 (레벨과 분리한 순수 최근폼)
                    "hot_off": _ewma(d["rs_g"]) - sea_rs,
                    "hot_def": sea_ra - _ewma(d["ra_g"]),      # +면 최근 실점 억제 좋아짐
                }

            fh, fa = feat(h), feat(a)
            sh = starter.get(g["gameId"], {}).get(h)
            sa = starter.get(g["gameId"], {}).get(a)
            if fh and fa:
                rows.append({
                    "season": s,
                    "pyth_diff": fh["pyth"] - fa["pyth"],
                    "form_diff": fh["form"] - fa["form"],
                    "starter_diff": (sh - sa) if (sh is not None and sa is not None) else 0.0,
                    "has_starter": sh is not None and sa is not None,
                    # 최근가중 투타(결과) 피처
                    "recent_off_diff": fh["ewma_rs"] - fa["ewma_rs"],     # 최근 타격(득점)
                    "recent_def_diff": fa["ewma_ra"] - fh["ewma_ra"],     # 최근 투수(실점 억제)
                    "recent_rd_diff": (fh["ewma_rs"] - fh["ewma_ra"])
                                      - (fa["ewma_rs"] - fa["ewma_ra"]),  # 최근 득실차
                    "hot_diff": (fh["hot_off"] + fh["hot_def"])
                                - (fa["hot_off"] + fa["hot_def"]),        # 순수 '핫/콜드'
                    "final_pyth_diff": final_pyth[(s, h)] - final_pyth[(s, a)],
                    "home_win": int(hs > as_),
                })
            hw = int(hs > as_)
            st[h]["rs"] += hs; st[h]["ra"] += as_; st[h]["wins"].append(hw)
            st[h]["rs_g"].append(hs); st[h]["ra_g"].append(as_)
            st[a]["rs"] += as_; st[a]["ra"] += hs; st[a]["wins"].append(1 - hw)
            st[a]["rs_g"].append(as_); st[a]["ra_g"].append(hs)
    return rows


def main() -> None:
    rows = build()
    y = np.array([r["home_win"] for r in rows])
    print(f"표본 {len(y)}경기 (2021~2025) · 홈승률(naive 기저) {y.mean():.3f}\n")

    def cv(feats, clf=None, mask=None):
        X = np.array([[r[f] for f in feats] for r in rows])
        yy = y
        if mask:
            m = np.array([r[mask] for r in rows])
            X, yy = X[m], y[m]
        if clf is None:
            clf = make_pipeline(StandardScaler(), LogisticRegression())
        acc = cross_val_score(clf, X, yy, cv=5, scoring="accuracy")
        return acc.mean(), acc.std(), len(yy)

    print("── 정직한 point-in-time (미래 정보 없음) ──")
    for label, feats in [("LR 피타만", ["pyth_diff"]),
                         ("LR 피타+폼", ["pyth_diff", "form_diff"]),
                         ("LR 피타+폼+선발", ["pyth_diff", "form_diff", "starter_diff"])]:
        m, sd, _ = cv(feats)
        print(f"  {label:16} {m:.3f} (±{sd:.3f})")
    rf = RandomForestClassifier(n_estimators=200, max_depth=5, random_state=0)
    m, sd, _ = cv(["pyth_diff", "form_diff", "starter_diff"], clf=rf)
    print(f"  {'랜덤포레스트':16} {m:.3f} (±{sd:.3f})  ← 복잡한 모델은 손해")

    print("\n── 최근가중(EWMA) 투타 피처 추가 (사용자 제안 검증, 정직한 시점별) ──")
    base = ["pyth_diff", "form_diff", "starter_diff"]
    for label, feats in [
        ("기준: 피타+폼+선발", base),
        ("+ 최근 타격(득점가중)", base + ["recent_off_diff"]),
        ("+ 최근 투수(실점가중)", base + ["recent_def_diff"]),
        ("+ 최근 투타 둘다", base + ["recent_off_diff", "recent_def_diff"]),
        ("+ 최근 득실차(EWMA)", base + ["recent_rd_diff"]),
        ("+ 순수 핫/콜드 델타", base + ["hot_diff"]),
        ("최근가중만(피타 대체)", ["recent_rd_diff", "form_diff", "starter_diff"]),
    ]:
        m, sd, _ = cv(feats)
        print(f"  {label:22} {m:.3f} (±{sd:.3f})")

    print("\n── 누수(leakage): 시즌 '최종' 집계를 피처로 k-fold ──")
    m, sd, _ = cv(["final_pyth_diff"])
    print(f"  시즌최종 피타차(누수)   {m:.3f} (±{sd:.3f})")
    m, sd, _ = cv(["pyth_diff", "final_pyth_diff"])
    print(f"  시점별+시즌최종 혼합    {m:.3f}")
    print("\n→ 정직한 천장 ~0.56, 누수 허용 시 ~0.58~0.59 (논문 59.3%와 근접).")


if __name__ == "__main__":
    main()
