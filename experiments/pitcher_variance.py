# -*- coding: utf-8 -*-
"""
pitcher_variance.py — 선발투수 '실점 변동성(안전형 vs 폭발형)'의 예측 가치 검증
================================================================================

문제 제기(레퍼런스): 기대 스코어가 선발 평균 ERA·이닝에 과하게 묶여 단조롭다.
같은 평균이라도 "꾸준히 6이닝 2~3실점" 투수와 "5이닝 무실점 or 3이닝 7실점"으로
널뛰는 투수는 경기 결과의 분포(꼬리)가 다르다 — 후자에 높은 분산을 주면 더 역동적.

그러나 '역동성 ≠ 정확도'. 숫자를 억지로 벌리면 캘리브레이션이 무너진다.
그래서 선발의 '등판별 실점 표준편차'가 실제로 정보를 갖는지 세 가지로 검증한다.

  A. 승부 예측:  평균 RA 차이에 '변동성 차이'를 더하면 홀드아웃 로그로스/Brier가
                 개선되는가? (개선 없으면 승부 예측용으로는 안 넣음)
  B. 분산 검증:  고변동 선발 경기가 실제로 총득점·마진이 더 퍼지는가?
                 (밴드 폭을 넓힐 근거)
  C. 우세팀 압축: 우세팀의 선발이 고변동일 때, 그 우세팀이 평균 기대보다 덜 이기는가?
                 (승률을 50%쪽으로 압축할 근거 = 캘리브레이션 개선)

시점(point-in-time)만 사용 — 각 경기의 피처는 '그 경기 이전' 등판만으로 계산(누수 없음).

실행:  .venv/bin/python experiments/pitcher_variance.py [시즌...]
       (기본 2025. 예: ... pitcher_variance.py 2023 2024 2025)
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, brier_score_loss

MIN_STARTS = 5          # 변동성이 안정되려면 사전 등판 5회+ 필요
FAV_EDGE = 0.7          # '뚜렷한 우세팀' 기준: 선발 평균 실점차 0.7점+ (Test C)


def load_starts(season: int):
    """(game_meta, starts) 반환.
    game_meta: gid -> dict(date, home, away, hs, as_)
    starts:    (gid, team) -> dict(date, pcode, runs)  # 그 팀 '선발'의 그 경기 실점
    """
    games = naver_games.filter_regular_season(naver_games.fetch_season_games(season))
    done = [g for g in games if g.get("statusCode") == "RESULT" and not g.get("cancel")]
    meta = {}
    for g in done:
        hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
        if hs is None or as_ is None:
            continue
        meta[str(g["gameId"])] = {
            "date": g["gameDate"], "home": g["homeTeamCode"], "away": g["awayTeamCode"],
            "hs": int(hs), "as_": int(as_)}
    box = boxscore.collect_season_pitching(done)
    box["game_id"] = box["game_id"].astype(str)
    # 선발 = 그 (경기, 팀)에서 '첫 투수'(box는 등판 순서 보존)
    firsts = box.drop_duplicates(["game_id", "team"], keep="first")
    starts = {}
    for _, row in firsts.iterrows():
        gid = str(row["game_id"])
        if gid not in meta:
            continue
        starts[(gid, str(row["team"]))] = {
            "date": row["date"], "pcode": str(row["pcode"]), "runs": float(row["r"])}
    return meta, starts


def pit_features(meta: dict, starts: dict):
    """각 경기에 대해 두 선발의 '사전(point-in-time)' 평균·표준편차 실점을 붙인 행 리스트.
    두 선발 모두 사전 등판 MIN_STARTS+ 있는 경기만."""
    # pcode -> 시간순 (date, gid, runs) 등판열
    hist = defaultdict(list)
    for (gid, team), s in starts.items():
        hist[s["pcode"]].append((s["date"], gid, s["runs"]))
    for p in hist:
        hist[p].sort()
    # pcode+gid -> (사전 평균, 사전 표준편차)  — 그 경기 '이전' 등판만
    prior = {}
    for p, seq in hist.items():
        runs = [r for _, _, r in seq]
        for i, (_, gid, _) in enumerate(seq):
            if i >= MIN_STARTS:
                past = np.array(runs[:i], dtype=float)
                prior[(p, gid)] = (float(past.mean()), float(past.std(ddof=1)))

    rows = []
    for gid, m in meta.items():
        sh, sa = starts.get((gid, m["home"])), starts.get((gid, m["away"]))
        if not sh or not sa:
            continue
        ph, pa = prior.get((sh["pcode"], gid)), prior.get((sa["pcode"], gid))
        if not ph or not pa:
            continue
        mean_h, std_h = ph
        mean_a, std_a = pa
        rows.append({
            "gid": gid, "date": m["date"],
            "mean_h": mean_h, "std_h": std_h, "mean_a": mean_a, "std_a": std_a,
            # 상대 선발이 잘 내줄수록 우리 팀이 득점 → 홈 마진은 (원정선발평균 − 홈선발평균)에 비례
            "mean_diff": mean_a - mean_h,         # +면 홈 우세
            "var_diff": std_a - std_h,            # +면 원정선발이 더 폭발형
            "var_sum": std_h + std_a,             # 경기 전체 변동성
            "home_win": 1 if m["hs"] > m["as_"] else (0 if m["hs"] < m["as_"] else None),
            "total": m["hs"] + m["as_"], "margin": m["hs"] - m["as_"],
        })
    return rows


def _fit_eval(train, test, feats):
    X_tr = np.array([[r[f] for f in feats] for r in train])
    y_tr = np.array([r["home_win"] for r in train])
    X_te = np.array([[r[f] for f in feats] for r in test])
    y_te = np.array([r["home_win"] for r in test])
    clf = LogisticRegression(max_iter=1000)
    clf.fit(X_tr, y_tr)
    p = clf.predict_proba(X_te)[:, 1]
    acc = float(((p >= 0.5).astype(int) == y_te).mean())
    return {"logloss": log_loss(y_te, p), "brier": brier_score_loss(y_te, p),
            "acc": acc, "n": len(y_te)}


def main(seasons):
    print(f"=== 선발 실점 변동성 검증 · 시즌 {seasons} · 사전등판≥{MIN_STARTS} ===\n")
    rows = []
    for s in seasons:
        meta, starts = load_starts(s)
        r = pit_features(meta, starts)
        for x in r:
            x["season"] = s
        rows += r
        print(f"  {s}: 사용 경기 {len(r)}")
    rows = [r for r in rows if r["home_win"] is not None]       # 무승부 제외
    rows.sort(key=lambda r: (r["date"], r["gid"]))
    n = len(rows)
    print(f"\n총 사용 경기(무승부 제외): {n}")
    if n < 200:
        print("표본이 작아 결론 유보. 시즌을 더 주세요."); return

    # ── 사전 진단: 선발 변동성 자체가 얼마나 퍼져 있나 ──
    stds = np.array([r["std_h"] for r in rows] + [r["std_a"] for r in rows])
    print(f"선발 등판별 실점 std 분포: 중앙값 {np.median(stds):.2f} "
          f"({np.percentile(stds,25):.2f}~{np.percentile(stds,75):.2f}), "
          f"평균 실점 {np.mean([r['mean_h'] for r in rows]+[r['mean_a'] for r in rows]):.2f}")

    # ── Test A: 변동성이 승부 예측을 개선하나 (홀드아웃) ──
    cut = int(n * 0.7)
    train, test = rows[:cut], rows[cut:]
    base = _fit_eval(train, test, ["mean_diff"])
    plusv = _fit_eval(train, test, ["mean_diff", "var_diff"])
    # 압축 가설의 직접 피처: 우세폭 × 변동성 상호작용
    for r in rows:
        r["fav_edge_x_var"] = abs(r["mean_diff"]) * r["var_sum"]
    base2 = _fit_eval(train, test, ["mean_diff"])
    plusx = _fit_eval(train, test, ["mean_diff", "var_diff", "fav_edge_x_var"])
    print("\n[A] 승부 예측 (앞 70% 학습 → 뒤 30% 검증, 낮을수록 좋음)")
    print(f"  기준(평균만)          logloss {base['logloss']:.4f}  brier {base['brier']:.4f}  acc {base['acc']*100:.1f}%  (n={base['n']})")
    print(f"  +변동성차             logloss {plusv['logloss']:.4f}  brier {plusv['brier']:.4f}  acc {plusv['acc']*100:.1f}%")
    print(f"  +변동성차+우세×변동    logloss {plusx['logloss']:.4f}  brier {plusx['brier']:.4f}  acc {plusx['acc']*100:.1f}%")
    dll = base["logloss"] - plusx["logloss"]
    print(f"  → 로그로스 개선 {dll:+.4f} "
          f"({'개선 있음' if dll > 0.0015 else '사실상 없음(승부 예측용으론 불필요)'})")

    # ── Test B: 고변동 선발 경기가 실제로 결과가 더 퍼지나 ──
    med = np.median([r["var_sum"] for r in rows])
    lo = [r for r in rows if r["var_sum"] <= med]
    hi = [r for r in rows if r["var_sum"] > med]
    def _disp(g, k): return float(np.std([r[k] for r in g]))
    print(f"\n[B] 분산 검증 (경기 변동성 var_sum 중앙값 {med:.2f}로 저/고 분할)")
    print(f"  저변동({len(lo)}): 총득점 std {_disp(lo,'total'):.2f}  마진 std {_disp(lo,'margin'):.2f}")
    print(f"  고변동({len(hi)}): 총득점 std {_disp(hi,'total'):.2f}  마진 std {_disp(hi,'margin'):.2f}")
    b_ok = _disp(hi, "margin") - _disp(lo, "margin")
    print(f"  → 마진 std 차이 {b_ok:+.2f} "
          f"({'고변동 경기가 실제로 더 퍼짐 → 밴드 확대 근거 O' if b_ok > 0.15 else '유의미하지 않음'})")

    # ── Test C: 우세팀 선발이 고변동이면 덜 이기나 (압축 근거) ──
    favs = []
    for r in rows:
        if abs(r["mean_diff"]) < FAV_EDGE:
            continue
        home_fav = r["mean_diff"] > 0
        fav_std = r["std_h"] if home_fav else r["std_a"]
        fav_won = (r["home_win"] == 1) if home_fav else (r["home_win"] == 0)
        favs.append((fav_std, fav_won))
    if len(favs) >= 100:
        fmed = np.median([s for s, _ in favs])
        flo = [w for s, w in favs if s <= fmed]
        fhi = [w for s, w in favs if s > fmed]
        print(f"\n[C] 우세팀 압축 (선발평균차≥{FAV_EDGE}인 뚜렷한 우세팀 {len(favs)}경기, "
              f"우세팀선발 std 중앙값 {fmed:.2f})")
        print(f"  안전형 우세팀({len(flo)}): 승률 {100*np.mean(flo):.1f}%")
        print(f"  폭발형 우세팀({len(fhi)}): 승률 {100*np.mean(fhi):.1f}%")
        c_ok = np.mean(flo) - np.mean(fhi)
        print(f"  → 승률 차이 {100*c_ok:+.1f}%p "
              f"({'폭발형 우세팀이 덜 이김 → 승률 50%쪽 압축 근거 O' if c_ok > 0.03 else '유의미하지 않음'})")
    else:
        print(f"\n[C] 뚜렷한 우세팀 표본 부족({len(favs)}) — 유보")

    print("\n=== 종합 판정 ===")
    print(f"  A 승부예측 개선:   {'O' if dll > 0.0015 else 'X'}")
    print(f"  B 밴드확대 근거:   {'O' if b_ok > 0.15 else 'X'}")
    verdict_c = 'favs' in dir() and len(favs) >= 100 and (np.mean(flo) - np.mean(fhi)) > 0.03
    print(f"  C 승률압축 근거:   {'O' if verdict_c else 'X'}")


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    main(args or [2025])
