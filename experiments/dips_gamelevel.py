# -*- coding: utf-8 -*-
"""
dips_gamelevel.py — 선발 구위(DIPS) 보정을 '게임 레벨'에서 검증
================================================================

dips_starter.py는 '선발 실점' 레벨에서 구위(k_stuff)가 생 RA9보다 낫다는 걸 보였다.
여기서는 그 변경을 실제 기대스코어 모델 구조에 넣었을 때, 경기 승부 예측이
개선되는지(Brier·로그로스·캘리브레이션) + 예측이 실제로 더 벌어지는지(스프레드)를
game_projection과 동일한 수식으로 검증한다.

모델 구조(game_projection 그대로, 구장=중립):
  erH = lg · idx(oH) · idx(pA) · HOME_BOOST         (idx(v)=1+(v/base−1)·SHRINK)
  erA = lg · idx(oA) · idx(pH) / HOME_BOOST
  pitch_X = (SP_INN·선발기대RA9_X + BP_INN·팀불펜RA9_X)/9
  승률 = 로지스틱(margin=erH−erA) — 스케일은 train에서 적합(정보량 비교, 상수 튜닝 무관)

두 모델은 '선발 기대RA9' 산출만 다르다(나머지 전부 동일):
  A(현행) : 선발기대RA9 = f_A(사전RA9)                      [train 적합]
  B(제안) : 선발기대RA9 = f_B(사전RA9, 사전구위)             [train 적합]
f_·는 train에서 '이 등판 실점(per9)'을 회귀한 적합값(=기대RA9). 누수 없음.

전부 point-in-time. 데이터 전부 캐시(box·gamelog·경기목록).
실행:  .venv/bin/python experiments/dips_gamelevel.py [train시즌.. -- 검증시즌]
       기본: train 2023 2024, holdout 2025
"""

import sys
import warnings
from collections import defaultdict

sys.path.insert(0, "src")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import numpy as np
import naver_games
import boxscore
import kbostuff_client as kc
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import log_loss, brier_score_loss
from game_projection import SHRINK, HOME_BOOST, SP_INN, BP_INN

MIN_TEAM_G = 10          # 팀 사전 경기 10+
MIN_SP_OUTS = 30         # 선발 사전 아웃 30+
MIN_SP_PITCH = 200       # 선발 사전 투구 200+


def season_data(season):
    games = naver_games.filter_regular_season(naver_games.fetch_season_games(season))
    done = [g for g in games if g.get("statusCode") == "RESULT" and not g.get("cancel")]
    meta = []
    for g in done:
        hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
        if hs is None or as_ is None:
            continue
        meta.append({"gid": str(g["gameId"]), "date": g["gameDate"],
                     "home": g["homeTeamCode"], "away": g["awayTeamCode"],
                     "hs": int(hs), "as_": int(as_)})
    box = boxscore.collect_season_pitching(done)
    box["game_id"] = box["game_id"].astype(str)
    box["outs"] = box["inn"].map(boxscore._innings_to_outs)
    # (gid,team) -> 선발행 / 불펜합
    starter, pen = {}, {}
    for (gid, team), g in box.groupby(["game_id", "team"]):
        g = g.reset_index(drop=True)
        starter[(gid, team)] = {"pcode": str(g.loc[0, "pcode"]),
                                "r": float(g.loc[0, "r"]), "outs": int(g.loc[0, "outs"])}
        rest = g.iloc[1:]
        pen[(gid, team)] = {"r": float(rest["r"].sum()), "outs": int(rest["outs"].sum())}
    # 구위
    gl = kc.fetch_pitching_game_log(season).dropna(subset=["k_stuff_v2", "n_pitches"]).copy()
    gl["pitcher_pcode"] = gl["pitcher_pcode"].astype(str); gl["game_id"] = gl["game_id"].astype(str)
    base = kc.daily_league_stuff(gl); gl["ks"] = gl["k_stuff_v2"] - gl["game_date"].map(base) + 100.0
    stuff = {(str(r.game_id), str(r.pitcher_pcode)): (float(r.ks), float(r.n_pitches))
             for r in gl.itertuples()}
    return meta, starter, pen, stuff


def build(seasons):
    """point-in-time 게임 행 생성. 각 행: 팀오프·팀불펜RA9·선발(사전RA9·구위)·결과·이등판실점."""
    rows = []
    for s in seasons:
        meta, starter, pen, stuff = season_data(s)
        meta.sort(key=lambda m: (m["date"], m["gid"]))
        # 누적기(시즌 리셋)
        tRS, tG = defaultdict(float), defaultdict(int)             # 팀 득점·경기
        lgRS, lgG = 0.0, 0
        penR, penO = defaultdict(float), defaultdict(float)         # 팀 불펜 실점·아웃
        spR, spO = defaultdict(float), defaultdict(float)           # 선발 실점·아웃(pcode)
        spKS, spP = defaultdict(float), defaultdict(float)          # 선발 구위가중합·투구(pcode)
        for m in meta:
            gid, h, a = m["gid"], m["home"], m["away"]
            sh, sa = starter.get((gid, h)), starter.get((gid, a))
            ph, pa = pen.get((gid, h)), pen.get((gid, a))
            if not (sh and sa and ph and pa):
                continue
            ready = (tG[h] >= MIN_TEAM_G and tG[a] >= MIN_TEAM_G and lgG > 0)
            pch, pca = sh["pcode"], sa["pcode"]
            sp_ok = (spO[pch] >= MIN_SP_OUTS and spO[pca] >= MIN_SP_OUTS
                     and spP[pch] >= MIN_SP_PITCH and spP[pca] >= MIN_SP_PITCH)
            if ready and sp_ok and penO[h] > 30 and penO[a] > 30:
                lg = lgRS / lgG
                rows.append({
                    "season": s, "date": m["date"],
                    "lg": lg,
                    "oH": tRS[h] / tG[h], "oA": tRS[a] / tG[a],
                    "bpH": penR[h] * 27 / penO[h], "bpA": penR[a] * 27 / penO[a],
                    "spRA_h": spR[pch] * 27 / spO[pch], "spRA_a": spR[pca] * 27 / spO[pca],
                    "spKS_h": spKS[pch] / spP[pch], "spKS_a": spKS[pca] / spP[pca],
                    # 선발 회귀 학습 타깃: 이 등판 실점 per9(캡)
                    "tgt_h": min(15.0, sh["r"] * 27 / sh["outs"]) if sh["outs"] else None,
                    "tgt_a": min(15.0, sa["r"] * 27 / sa["outs"]) if sa["outs"] else None,
                    "home_win": 1 if m["hs"] > m["as_"] else (0 if m["hs"] < m["as_"] else None),
                })
            # 누적 갱신(이 경기 포함)
            tRS[h] += m["hs"]; tRS[a] += m["as_"]; tG[h] += 1; tG[a] += 1
            lgRS += m["hs"] + m["as_"]; lgG += 2
            penR[h] += ph["r"]; penO[h] += ph["outs"]; penR[a] += pa["r"]; penO[a] += pa["outs"]
            spR[pch] += sh["r"]; spO[pch] += sh["outs"]; spR[pca] += sa["r"]; spO[pca] += sa["outs"]
            st_h, st_a = stuff.get((gid, pch)), stuff.get((gid, pca))
            if st_h:
                spKS[pch] += st_h[0] * st_h[1]; spP[pch] += st_h[1]
            if st_a:
                spKS[pca] += st_a[0] * st_a[1]; spP[pca] += st_a[1]
    return [r for r in rows if r["home_win"] is not None]


def _idx(v, base):
    return 1 + (v / base - 1) * SHRINK


def _margins(rows, est):
    """est(사전RA9, 구위or None)->선발기대RA9. 각 행의 margin=erH−erA 반환."""
    out = []
    for r in rows:
        seh = est(r["spRA_h"], r.get("spKS_h"))
        sea = est(r["spRA_a"], r.get("spKS_a"))
        pitchH = (SP_INN * seh + BP_INN * r["bpH"]) / 9
        pitchA = (SP_INN * sea + BP_INN * r["bpA"]) / 9
        erH = r["lg"] * _idx(r["oH"], r["lg"]) * _idx(pitchA, r["lg"]) * HOME_BOOST
        erA = r["lg"] * _idx(r["oA"], r["lg"]) * _idx(pitchH, r["lg"]) / HOME_BOOST
        out.append(erH - erA)
    return np.array(out)


def _starter_estimators(train):
    """train 등판(양팀 각각)으로 '기대 선발RA9' 회귀 2종 적합."""
    X_ra, X_rs, y = [], [], []
    for r in train:
        for side in ("h", "a"):
            t = r[f"tgt_{side}"]
            if t is None:
                continue
            X_ra.append([r[f"spRA_{side}"]])
            X_rs.append([r[f"spRA_{side}"], r[f"spKS_{side}"]])
            y.append(t)
    A = LinearRegression().fit(np.array(X_ra), np.array(y))       # 사전RA9만
    B = LinearRegression().fit(np.array(X_rs), np.array(y))       # +구위
    return (lambda ra, ks: float(A.predict([[ra]])[0]),
            lambda ra, ks: float(B.predict([[ra, ks]])[0]))


def _calib(p, y, bins=5):
    q = np.quantile(p, np.linspace(0, 1, bins + 1))
    q[0] -= 1e-9
    lines = []
    for i in range(bins):
        m = (p > q[i]) & (p <= q[i + 1])
        if m.sum():
            lines.append(f"    {q[i]:.2f}~{q[i+1]:.2f}: 예측 {p[m].mean()*100:4.1f}%  실제 {y[m].mean()*100:4.1f}%  (n={m.sum()})")
    return "\n".join(lines)


def main(train_seasons, holdout):
    print(f"=== 선발 DIPS 게임레벨 검증 · train {train_seasons} → holdout {holdout} ===\n")
    allrows = build(sorted(set(train_seasons + [holdout])))
    train = [r for r in allrows if r["season"] in train_seasons]
    test = [r for r in allrows if r["season"] == holdout]
    print(f"사용 경기: train {len(train)}  holdout {len(test)}")
    if len(train) < 200 or len(test) < 100:
        print("표본 부족 — 유보."); return

    estA, estB = _starter_estimators(train)
    yb_tr = np.array([r["home_win"] for r in train]); yb_te = np.array([r["home_win"] for r in test])

    def _winp(margin, scale, hb=0.03):
        # 프로덕션과 동일: 고정 스케일 로지스틱(+홈 상수 가산). margin은 이미 홈보정 포함.
        return 1.0 / (1.0 + np.exp(-margin / scale))

    res = {}
    for name, est in (("A(현행:RA9)", estA), ("B(제안:RA9+구위)", estB)):
        mtr = _margins(train, est); mte = _margins(test, est)
        # 프로덕션식 '고정 스케일' — train brier 최소가 되는 스케일 선택(모델별 공정 캘리브레이션)
        best_s, best_b = None, 1e9
        for s in np.arange(2.0, 12.1, 0.25):
            b = brier_score_loss(yb_tr, _winp(mtr, s))
            if b < best_b:
                best_b, best_s = b, s
        p = _winp(mte, best_s)
        res[name] = {"p": p, "margin": mte, "scale": best_s,
                     "ll": log_loss(yb_te, p), "brier": brier_score_loss(yb_te, p),
                     "acc": float(((p >= .5).astype(int) == yb_te).mean())}

    print("\n[승부 예측 · holdout] 낮을수록 좋음 (스케일=train brier 최소로 캘리브레이션)")
    for name in res:
        r = res[name]
        print(f"  {name:16s} logloss {r['ll']:.4f}  brier {r['brier']:.4f}  acc {r['acc']*100:.1f}%  (scale {r['scale']:.2f})")
    dll = res["A(현행:RA9)"]["ll"] - res["B(제안:RA9+구위)"]["ll"]
    dbr = res["A(현행:RA9)"]["brier"] - res["B(제안:RA9+구위)"]["brier"]
    print(f"  → 개선  logloss {dll:+.4f}  brier {dbr:+.4f}  "
          f"({'개선' if (dll>0.0005 or dbr>0.0003) else '사실상 없음'})")

    print("\n[다이내미즘 · 예측이 얼마나 벌어지나]")
    for name in res:
        r = res[name]
        print(f"  {name:16s} margin std {np.std(r['margin']):.3f}  승률 std {np.std(r['p'])*100:.1f}%p  "
              f"승률 5~95% 범위 {np.percentile(r['p'],5)*100:.0f}~{np.percentile(r['p'],95)*100:.0f}%")

    print("\n[캘리브레이션 · B(제안)]")
    print(_calib(res["B(제안:RA9+구위)"]["p"], yb_te))


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    if len(args) >= 2:
        main(args[:-1], args[-1])
    else:
        main([2023, 2024], 2025)
