# -*- coding: utf-8 -*-
"""
dips_starter.py — 선발 '구위(운 독립, DIPS)'가 생 RA9보다 실점을 더 잘 예측하나
================================================================================

세이버매트릭스 중심 이론 DIPS(McCracken 2001): 투수의 ERA/RA9는 인플레이 타구
결과(BABIP)·수비·시퀀싱 운이 잔뜩 껴 있어 노이즈가 크다. 반면 삼진·볼넷·구위
(fielding-independent)는 투수 본인의 반복가능한 스킬이라 '미래 실점'을 더 잘 예측한다.

우리 현실:
  · 기대스코어 모델의 선발 입력 = 생 RA9 (game_projection `_game_ra9`).
  · 그런데 우리 자신의 백테스트(single_game_prediction)는 '선발 K-Stuff'가
    승부 예측을 +2.6%p 올린 걸 이미 확인. 즉 구위엔 실재 신호가 있는데
    기대스코어엔 안 꽂혀 있다.

질문: 선발의 사전 구위(k_stuff_v2, 리그 재센터·투구수가중)를 생 RA9에 더하면,
      '이 경기 선발 실점'을 더 잘 예측하는가? (개선되면 정확도↑ + 같은 RA9 선발을
      구위로 갈라 매치업 스프레드↑ = 다이내미즘)

전부 point-in-time(그 경기 이전 등판만). 데이터: box(등판별 실점) + kbostuff
게임로그(등판별 구위, 캐시).

실행:  .venv/bin/python experiments/dips_starter.py [시즌...]   (기본 2023 2024 2025)
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
from sklearn.linear_model import LinearRegression

MIN_STARTS = 5           # 사전 등판 5회+
MIN_PITCHES = 200        # 사전 투구수 200+ (구위 안정화)


def load_season(season):
    """starter starts: list of dict(date, pcode, r, outs, ks) — ks=재센터 구위(없으면 None)."""
    games = naver_games.filter_regular_season(naver_games.fetch_season_games(season))
    done = [g for g in games if g.get("statusCode") == "RESULT" and not g.get("cancel")]
    box = boxscore.collect_season_pitching(done)
    box["game_id"] = box["game_id"].astype(str)
    box["outs"] = box["inn"].map(boxscore._innings_to_outs)
    firsts = box.drop_duplicates(["game_id", "team"], keep="first")   # 선발

    # 구위 게임로그(재센터: 리그 일별 평균 제거 → 스케일 드리프트 제거)
    gl = kc.fetch_pitching_game_log(season).dropna(subset=["k_stuff_v2", "n_pitches"]).copy()
    gl["pitcher_pcode"] = gl["pitcher_pcode"].astype(str)
    gl["game_id"] = gl["game_id"].astype(str)
    base = kc.daily_league_stuff(gl)
    gl["ks"] = gl["k_stuff_v2"] - gl["game_date"].map(base) + 100.0
    stuff = {(str(r.game_id), str(r.pitcher_pcode)): (float(r.ks), float(r.n_pitches))
             for r in gl.itertuples()}

    starts = []
    for r in firsts.itertuples():
        gid, pc = str(r.game_id), str(r.pcode)
        if int(r.outs) < 3:          # 실질 등판만
            continue
        ks = stuff.get((gid, pc))
        starts.append({"date": str(r.date), "pcode": pc, "r": float(r.r),
                       "outs": int(r.outs), "ks": ks[0] if ks else None,
                       "pit": ks[1] if ks else 0.0})
    return starts


def build(seasons):
    rows = []
    for s in seasons:
        starts = load_season(s)
        # pcode별 시간순 → 사전 누적
        by = defaultdict(list)
        for x in starts:
            by[x["pcode"]].append(x)
        for pc, seq in by.items():
            seq.sort(key=lambda x: x["date"])
            cum_r = cum_o = 0.0
            ks_wsum = pit_sum = 0.0
            nprev = 0
            for x in seq:
                if nprev >= MIN_STARTS and cum_o >= 30 and pit_sum >= MIN_PITCHES and x["ks"] is not None:
                    rows.append({
                        "season": s, "date": x["date"],
                        "prior_ra9": cum_r * 27.0 / cum_o,
                        "prior_stuff": ks_wsum / pit_sum,        # 높을수록 구위 좋음
                        "runs": x["r"],                          # 타깃: 이 경기 선발 실점
                    })
                # 누적 갱신(이 경기 포함 → 다음 경기부터)
                cum_r += x["r"]; cum_o += x["outs"]; nprev += 1
                if x["ks"] is not None:
                    ks_wsum += x["ks"] * x["pit"]; pit_sum += x["pit"]
    return rows


def _eval(train, test, feats):
    Xtr = np.array([[r[f] for f in feats] for r in train]); ytr = np.array([r["runs"] for r in train])
    Xte = np.array([[r[f] for f in feats] for r in test]); yte = np.array([r["runs"] for r in test])
    m = LinearRegression().fit(Xtr, ytr)
    p = m.predict(Xte)
    return (float(np.sqrt(np.mean((p - yte) ** 2))), float(np.mean(np.abs(p - yte))),
            dict(zip(feats, m.coef_)))


def main(seasons):
    print(f"=== 선발 DIPS(구위) 검증 · 시즌 {seasons} · 사전등판≥{MIN_STARTS}·투구≥{MIN_PITCHES} ===\n")
    rows = build(seasons)
    rows.sort(key=lambda r: (r["date"],))
    n = len(rows)
    print(f"사용 선발등판(구위 매칭): {n}  (평균 선발실점 {np.mean([r['runs'] for r in rows]):.2f})")
    if n < 400:
        print("표본 부족 — 결론 유보."); return

    ra = np.array([r["prior_ra9"] for r in rows]); stf = np.array([r["prior_stuff"] for r in rows])
    y = np.array([r["runs"] for r in rows])
    print("\n[상관] 사전지표 vs 이 경기 선발실점 (|r| 클수록 예측력)")
    print(f"  사전 RA9   r = {np.corrcoef(ra, y)[0,1]:+.3f}")
    print(f"  사전 구위  r = {np.corrcoef(stf, y)[0,1]:+.3f}  (음수 정상: 구위↑→실점↓)")
    print(f"  (RA9와 구위의 상관 {np.corrcoef(ra, stf)[0,1]:+.3f} — 낮을수록 독립 정보)")

    # 홀드아웃: 앞 시즌 학습 → 마지막 시즌 검증
    last = seasons[-1]
    train = [r for r in rows if r["season"] != last]; test = [r for r in rows if r["season"] == last]
    if len(train) < 200 or len(test) < 100:
        cut = int(n * 0.7); train, test = rows[:cut], rows[cut:]; split = "앞70%→뒤30%"
    else:
        split = f"{[s for s in seasons if s!=last]}→{last}"
    print(f"\n[홀드아웃 {split}] '이 경기 선발실점' 예측오차 (낮을수록 좋음)")
    b_r, b_m, _ = _eval(train, test, ["prior_ra9"])
    s_r, s_m, coef = _eval(train, test, ["prior_ra9", "prior_stuff"])
    only_r, only_m, _ = _eval(train, test, ["prior_stuff"])
    print(f"  RA9만            RMSE {b_r:.3f}  MAE {b_m:.3f}")
    print(f"  구위만           RMSE {only_r:.3f}  MAE {only_m:.3f}")
    print(f"  RA9+구위         RMSE {s_r:.3f}  MAE {s_m:.3f}  (구위계수 {coef['prior_stuff']:+.3f})")
    print(f"  → RA9 대비 개선  RMSE {b_r - s_r:+.4f}  MAE {b_m - s_m:+.4f}")

    # leave-one-season-out 재현성
    print(f"\n[교차검증] 시즌별 홀드아웃 — RA9 vs RA9+구위 (MAE 개선)")
    ok = 0
    for ts in seasons:
        tr = [r for r in rows if r["season"] != ts]; te = [r for r in rows if r["season"] == ts]
        if len(te) < 80:
            continue
        _, bm, _ = _eval(tr, te, ["prior_ra9"]); _, sm, _ = _eval(tr, te, ["prior_ra9", "prior_stuff"])
        imp = bm - sm; ok += imp > 0
        print(f"  test {ts}: MAE {bm:.3f}->{sm:.3f} ({imp:+.4f})")
    print(f"\n=== 판정 ===  구위 추가가치: "
          f"{'O (RA9 대비 개선, 재현성 확인 필요)' if (b_m - s_m) > 0.005 else 'X'}")


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    main(args or [2023, 2024, 2025])
