# -*- coding: utf-8 -*-
"""
offense_metric.py — '더 나은 공격 지표'로 승부 예측이 오르나 (공격판 DIPS)
==========================================================================

문제: 기대스코어의 팀 공격 입력이 생 RS/G(시즌 득점/경기)다. RS/G는 시퀀싱·클러치·
상대 운이 껴 있다. 투수에서 RA9→구위(DIPS)가 통했듯, 공격도 운이 덜 낀 성분 지표
(팀 OPS 등)로 바꾸면 '이 경기 득점'과 '승부'를 더 잘 맞히지 않을까?

검증(전부 point-in-time, box_bat·경기스코어 캐시):
  Test1  각 팀 시즌누적 지표(RS/G vs OPS)가 '그 팀의 이 경기 득점'을 얼마나 예측?
  Test2  게임 승부: 공격=RS/G vs 공격=OPS(투수=팀 RA/G로 동일 고정) → 홀드아웃 Brier

실행:  .venv/bin/python experiments/offense_metric.py [train.. -- holdout]  (기본 23 24→25)
"""

import sys
import json
import warnings
from collections import defaultdict

sys.path.insert(0, "src")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import numpy as np
import naver_games
import config
from sklearn.linear_model import LinearRegression
from sklearn.metrics import brier_score_loss
from game_projection import SHRINK, HOME_BOOST

DATA = config.DATA_DIR
MIN_G = 12          # 팀 사전 경기 12+


def _box_bat(gid):
    try:
        return json.load(open(f"{DATA}/box_bat/{gid}.json", encoding="utf-8"))
    except Exception:
        return None


def build(seasons):
    """point-in-time 게임 행: 팀 RS/G·OPS·RA/G(사전) + 결과."""
    rows = []
    for s in seasons:
        g = naver_games.filter_official_teams(naver_games.filter_regular_season(
            naver_games.fetch_season_games(s)))
        meta = []
        for x in g:
            if x.get("statusCode") != "RESULT" or x.get("cancel"):
                continue
            hs, as_ = x.get("homeTeamScore"), x.get("awayTeamScore")
            if hs is None or as_ is None:
                continue
            meta.append((x["gameDate"], str(x["gameId"]), x["homeTeamCode"], x["awayTeamCode"],
                         int(hs), int(as_)))
        meta.sort()
        # 누적기(시즌 리셋)
        RS, RA, G = defaultdict(float), defaultdict(float), defaultdict(int)      # 득점·실점·경기
        AB, H, BB, TB = (defaultdict(float) for _ in range(4))                    # 성분(OPS용)
        lgRS, lgG = 0.0, 0
        lgAB, lgH, lgBB, lgTB = 0.0, 0.0, 0.0, 0.0
        for date, gid, h, a, hs, as_ in meta:
            if G[h] >= MIN_G and G[a] >= MIN_G and lgG > 0 and AB[h] > 0 and AB[a] > 0:
                lg = lgRS / lgG
                def ops(t):
                    obp = (H[t] + BB[t]) / (AB[t] + BB[t]) if (AB[t] + BB[t]) else 0
                    slg = TB[t] / AB[t] if AB[t] else 0
                    return obp + slg
                lg_ops = ((lgH + lgBB) / (lgAB + lgBB)) + (lgTB / lgAB)
                rows.append({
                    "season": s, "date": date,
                    "lg": lg,
                    "rsgH": RS[h] / G[h], "rsgA": RS[a] / G[a],
                    "ragH": RA[h] / G[h], "ragA": RA[a] / G[a],
                    "opsH": ops(h), "opsA": ops(a), "lg_ops": lg_ops,
                    "runsH": hs, "runsA": as_,
                    "home_win": 1 if hs > as_ else (0 if hs < as_ else None),
                })
            # 누적 갱신(이 경기 포함)
            RS[h] += hs; RS[a] += as_; RA[h] += as_; RA[a] += hs; G[h] += 1; G[a] += 1
            lgRS += hs + as_; lgG += 2
            bb = _box_bat(gid)
            if bb:
                for t in (h, a):
                    for r in [x for x in bb if x["team"] == t]:
                        AB[t] += int(r.get("ab", 0) or 0); H[t] += int(r.get("hit", 0) or 0)
                        BB[t] += int(r.get("bb", 0) or 0); TB[t] += int(r.get("tb", 0) or 0)
                        lgAB += int(r.get("ab", 0) or 0); lgH += int(r.get("hit", 0) or 0)
                        lgBB += int(r.get("bb", 0) or 0); lgTB += int(r.get("tb", 0) or 0)
    return [r for r in rows if r["home_win"] is not None]


def _idx(v, base):
    return 1 + (v / base - 1) * SHRINK


def main(train_s, holdout):
    print(f"=== 공격 지표 검증 · train {train_s} → holdout {holdout} ===\n")
    allrows = build(sorted(set(train_s + [holdout])))
    train = [r for r in allrows if r["season"] in train_s]
    test = [r for r in allrows if r["season"] == holdout]
    print(f"사용 경기: train {len(train)}  holdout {len(test)}")
    if len(test) < 100:
        print("표본 부족 — 유보."); return

    # ── Test1: 팀 지표가 '그 팀의 이 경기 득점'을 얼마나 예측하나 (팀-경기 단위) ──
    def teamgame(rows):
        rsg, ops, runs = [], [], []
        for r in rows:
            rsg += [r["rsgH"], r["rsgA"]]; ops += [r["opsH"], r["opsA"]]; runs += [r["runsH"], r["runsA"]]
        return np.array(rsg), np.array(ops), np.array(runs)
    rsg, ops, runs = teamgame(allrows)
    print("[Test1] 시즌누적 공격지표 vs '그 팀 이 경기 득점' 상관 (팀-경기)")
    print(f"  RS/G  r = {np.corrcoef(rsg, runs)[0,1]:+.3f}")
    print(f"  OPS   r = {np.corrcoef(ops, runs)[0,1]:+.3f}")

    # ── Test2: 게임 승부 Brier — 공격=RS/G vs OPS (투수=RA/G 동일 고정) ──
    yb_tr = np.array([r["home_win"] for r in train]); yb_te = np.array([r["home_win"] for r in test])

    def margins(rows, off):
        out = []
        for r in rows:
            if off == "rsg":
                oH, oA, base_o = r["rsgH"], r["rsgA"], r["lg"]
            else:  # ops → 리그 대비 지수로(스케일은 idx가 정규화)
                oH, oA, base_o = r["opsH"], r["opsA"], r["lg_ops"]
            # 투수(실점력)는 RA/G로 양 모델 공통
            pH, pA, base_p = r["ragH"], r["ragA"], r["lg"]
            erH = r["lg"] * _idx(oH, base_o) * _idx(pA, base_p) * HOME_BOOST
            erA = r["lg"] * _idx(oA, base_o) * _idx(pH, base_p) / HOME_BOOST
            out.append(erH - erA)
        return np.array(out)

    def winp(m, s): return 1 / (1 + np.exp(-m / s))
    def eval_off(off):
        mtr, mte = margins(train, off), margins(test, off)
        best = (7, 1e9)
        for s in np.arange(2, 14.05, .25):
            b = brier_score_loss(yb_tr, winp(mtr, s))
            if b < best[1]:
                best = (s, b)
        p = winp(mte, best[0])
        return {"brier": brier_score_loss(yb_te, p), "acc": float(((p >= .5).astype(int) == yb_te).mean()),
                "scale": best[0], "std": p.std() * 100}
    A, B = eval_off("rsg"), eval_off("ops")
    print("\n[Test2] 게임 승부 (공격지표만 교체, 투수=RA/G 고정 · 홀드아웃)")
    print(f"  공격=RS/G   Brier {A['brier']:.4f}  acc {A['acc']*100:.1f}%  승률std {A['std']:.1f}%p")
    print(f"  공격=OPS    Brier {B['brier']:.4f}  acc {B['acc']*100:.1f}%  승률std {B['std']:.1f}%p")
    print(f"  → OPS 개선  Brier {A['brier']-B['brier']:+.4f}  acc {(B['acc']-A['acc'])*100:+.1f}p")


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    if len(args) >= 2:
        main(args[:-1], args[-1])
    else:
        main([2023, 2024], 2025)
