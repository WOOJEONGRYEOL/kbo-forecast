# -*- coding: utf-8 -*-
"""
h2h_value.py — '상대전적(H2H)'이 팀 강도를 넘어 경기 결과를 예측하나
=====================================================================

단기전(가을야구) 승부 예측에서 '상대 X에 유독 강하다/약하다'(상대전적)의 비중을
늘려야 하나? 세이버매트릭스 정설은 H2H가 표본 노이즈(시즌 16경기)라 전체 강도 외
추가 예측력이 없다는 것. 우리 데이터로 직접 확인한다(최근폼·타선시너지 null과 같은 류).

검증(전부 point-in-time — 각 경기 피처는 '그 경기 이전' 성적만):
  strength_diff = 홈 시즌승률 − 원정 시즌승률 (그 경기 전까지)
  h2h           = 홈의 '상대 원정팀' 상대 승률 − 0.5 (그 시즌 사전 맞대결, ≥MIN_H2H)
  타깃: 홈 승(무승부 제외)
  Model A: strength_diff 만 / Model B: + h2h  → 홀드아웃 logloss·brier·acc 비교.
  H2H가 노이즈면 B가 A를 못 넘는다(또는 악화).

실행:  .venv/bin/python experiments/h2h_value.py [시즌...]   (기본 2023 2024 2025)
"""

import sys
import warnings
from collections import defaultdict

sys.path.insert(0, "src")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import numpy as np
import naver_games
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, brier_score_loss

MIN_GP = 20        # 두 팀 모두 시즌 20경기+ 소화(승률 안정화)
MIN_H2H = 4        # 사전 맞대결 4경기+ 있어야 h2h 피처 사용


def build(seasons):
    rows = []
    for s in seasons:
        g = naver_games.filter_official_teams(naver_games.filter_regular_season(
            naver_games.fetch_season_games(s)))
        done = [x for x in g if x.get("statusCode") in ("RESULT", "ENDED")
                and x.get("homeTeamScore") is not None]
        done.sort(key=lambda x: (x["gameDate"], x["gameId"]))
        W, L = defaultdict(int), defaultdict(int)                 # 팀 시즌 승/패
        h2hW, h2hN = defaultdict(int), defaultdict(int)           # (팀,상대) 승/맞대결수
        for x in done:
            h, a = x["homeTeamCode"], x["awayTeamCode"]
            hs, as_ = x["homeTeamScore"], x["awayTeamScore"]
            gp_h, gp_a = W[h] + L[h], W[a] + L[a]
            meet = h2hN[(h, a)]
            if gp_h >= MIN_GP and gp_a >= MIN_GP and meet >= MIN_H2H and hs != as_:
                rows.append({
                    "season": s, "date": x["gameDate"],
                    "strength_diff": W[h] / gp_h - W[a] / gp_a,
                    "h2h": h2hW[(h, a)] / meet - 0.5,            # 홈의 상대 승률 − 0.5
                    "home_win": 1 if hs > as_ else 0,
                })
            # 갱신(이 경기 포함)
            if hs > as_:
                W[h] += 1; L[a] += 1
            elif as_ > hs:
                W[a] += 1; L[h] += 1
            h2hN[(h, a)] += 1; h2hN[(a, h)] += 1
            if hs > as_:
                h2hW[(h, a)] += 1
            elif as_ > hs:
                h2hW[(a, h)] += 1
    return rows


def _eval(train, test, feats):
    Xtr = np.array([[r[f] for f in feats] for r in train]); ytr = np.array([r["home_win"] for r in train])
    Xte = np.array([[r[f] for f in feats] for r in test]); yte = np.array([r["home_win"] for r in test])
    m = LogisticRegression(max_iter=1000).fit(Xtr, ytr)
    p = m.predict_proba(Xte)[:, 1]
    return (log_loss(yte, p), brier_score_loss(yte, p),
            float(((p >= .5).astype(int) == yte).mean()), dict(zip(feats, m.coef_[0])))


def main(seasons):
    print(f"=== H2H(상대전적) 예측력 검증 · 시즌 {seasons} · 사전맞대결≥{MIN_H2H} ===\n")
    rows = build(seasons)
    n = len(rows)
    print(f"사용 경기: {n}")
    if n < 300:
        print("표본 부족 — 유보."); return

    h = np.array([r["h2h"] for r in rows]); y = np.array([r["home_win"] for r in rows])
    sd = np.array([r["strength_diff"] for r in rows])
    print(f"[상관] h2h vs 홈승 r = {np.corrcoef(h, y)[0,1]:+.3f}  "
          f"(참고 strength_diff vs 홈승 r = {np.corrcoef(sd, y)[0,1]:+.3f})")
    # 강도 통제 후 h2h 잔차상관: strength로 예측한 확률의 잔차 vs h2h
    from sklearn.linear_model import LogisticRegression as LR
    base = LR(max_iter=1000).fit(sd.reshape(-1, 1), y)
    resid = y - base.predict_proba(sd.reshape(-1, 1))[:, 1]
    print(f"[강도 통제 후] 잔차 vs h2h r = {np.corrcoef(h, resid)[0,1]:+.3f} (≈0이면 추가 정보 없음)")

    last = seasons[-1]
    tr = [r for r in rows if r["season"] != last]; te = [r for r in rows if r["season"] == last]
    if len(tr) < 150 or len(te) < 80:
        cut = int(n * 0.7); tr, te = rows[:cut], rows[cut:]; split = "앞70%→뒤30%"
    else:
        split = f"{[s for s in seasons if s!=last]}→{last}"
    aL, aB, aA, _ = _eval(tr, te, ["strength_diff"])
    bL, bB, bA, coef = _eval(tr, te, ["strength_diff", "h2h"])
    print(f"\n[홀드아웃 {split}] 낮을수록 좋음")
    print(f"  강도만        logloss {aL:.4f}  brier {aB:.4f}  acc {aA*100:.1f}%")
    print(f"  강도+H2H      logloss {bL:.4f}  brier {bB:.4f}  acc {bA*100:.1f}%  (h2h계수 {coef['h2h']:+.3f})")
    dll = aL - bL
    print(f"  → H2H 추가가치  logloss {dll:+.4f}  "
          f"({'개선' if dll > 0.0010 else '없음/악화 — 노이즈'})")

    print(f"\n=== 판정 ===  상대전적(H2H) 추가 예측력: {'O' if dll > 0.0010 else 'X (팀강도 넘는 신호 없음)'}")


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    main(args or [2023, 2024, 2025])
