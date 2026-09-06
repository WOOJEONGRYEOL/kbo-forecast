# -*- coding: utf-8 -*-
"""
lineup_synergy.py — '타선 시너지(타순 배치·연결성)'의 예측 가치 검증
=====================================================================

문제 제기(레퍼런스 #5): 기대 득점을 타자 평균(wRC+)으로만 보면 '연결성'이 빠진다.
상위타순 출루 + 중심타순 해결(OBP↑ then SLG↑)이라는 시퀀스가 같은 평균이라도
득점을 더 만든다 — 마르코프 체인식 득점 생산.

우리 모델은 이미 '상위 9명 PA가중 wRC+'라는 스칼라를 쓴다. 진짜 질문은:
   평균을 통제한 뒤에도 '타순 배치(시너지)'가 득점을 더 설명하는가?

검증(전부 point-in-time — 각 경기 피처는 '그 경기 이전' 누적만 사용, 누수 없음):
  타자 실력은 과거 시즌 CSV가 없어 box_bat(게임별 타순+기록) 캐시에서
  시즌 누적 OBP/SLG로 직접 계산. 타순 1~9 = 그 팀 '첫 9명'(등장 순서=타순).

  피처:
    base   = 선발 9명 단순 평균 OPS (평균 실력)
    synergy1 = 상위(1~2번) OBP × 중심(3~5번) SLG   (테이블세터×해결사)
    synergy2 = 상위집중도 = mean OPS(1~5) − mean OPS(6~9)
    balance  = 9명 OPS 표준편차 (균형 vs 편중)
  타깃: 그 경기 그 팀 실제 득점.

  판정: base만 vs base+synergy 홀드아웃(뒤 시즌) 예측오차(RMSE/MAE) 개선 여부.
        개선이 미미하면 '평균이 이미 다 잡고 있다' → 시너지 항 불채택.

실행:  .venv/bin/python experiments/lineup_synergy.py [시즌...]   (기본 2023 2024 2025)
"""

import sys
import json
import glob
import warnings
from collections import defaultdict

sys.path.insert(0, "src")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import numpy as np
import naver_games
import config
from sklearn.linear_model import LinearRegression

MIN_PA = 20             # 각 타자 사전 타석 20+ 있어야 그 경기 사용(실력 안정화)
DATA = config.DATA_DIR


def _load_box_bat(gid):
    try:
        return json.load(open(f"{DATA}/box_bat/{gid}.json", encoding="utf-8"))
    except Exception:
        return None


def season_games(season):
    """gid -> (date, home, away)  — 종료된 정규경기만, 날짜순."""
    g = naver_games.filter_regular_season(naver_games.fetch_season_games(season))
    out = {}
    for x in g:
        if x.get("statusCode") != "RESULT" or x.get("cancel"):
            continue
        out[str(x["gameId"])] = (x["gameDate"], x["homeTeamCode"], x["awayTeamCode"])
    return out


def build_rows(season):
    games = season_games(season)
    order = sorted(games.items(), key=lambda kv: (kv[1][0], kv[0]))   # 날짜순
    # 선수 누적(시점) — 시즌 리셋. pcode -> [ab,h,bb,tb,pa]
    acc = defaultdict(lambda: [0, 0, 0, 0, 0])
    rows = []
    for gid, (date, home, away) in order:
        bb = _load_box_bat(gid)
        if not bb:
            continue
        # 팀별 등장 순서(=타순) 유지
        byteam = defaultdict(list)
        for r in bb:
            byteam[r["team"]].append(r)
        for team, lst in byteam.items():
            # 선발 9 = 첫 등장 9개 pcode(중복 제외, 순서 유지)
            seen, slots = set(), []
            for r in lst:
                p = str(r["pcode"])
                if p in seen:
                    continue
                seen.add(p); slots.append(r)
                if len(slots) == 9:
                    break
            if len(slots) < 9:
                continue
            # 사전 누적으로 각 슬롯 OBP/SLG (미달이면 이 경기 스킵)
            obp, slg, ops = [], [], []
            ok = True
            for r in slots:
                a, h, w, tb, pa = acc[str(r["pcode"])]
                if pa < MIN_PA:
                    ok = False; break
                o = (h + w) / (a + w) if (a + w) else 0.0
                s = tb / a if a else 0.0
                obp.append(o); slg.append(s); ops.append(o + s)
            team_runs = sum(int(x.get("run", 0) or 0) for x in lst)   # 그 팀 실제 득점
            if ok:
                ops = np.array(ops)
                rows.append({
                    "season": season, "date": date, "gid": gid, "team": team,
                    "runs": float(team_runs),
                    "base": float(ops.mean()),
                    "syn1": float(np.mean(obp[:2]) * np.mean(slg[2:5])),   # 상위출루×중심장타
                    "syn2": float(ops[:5].mean() - ops[5:].mean()),        # 상위집중도
                    "balance": float(ops.std()),                          # 편중도
                })
        # 경기 후 누적 갱신(이 경기 기록을 다음 경기부터 반영 = 시점 유지)
        for r in bb:
            p = str(r["pcode"])
            a = acc[p]
            ab = int(r.get("ab", 0) or 0); hit = int(r.get("hit", 0) or 0)
            wbb = int(r.get("bb", 0) or 0); tb = int(r.get("tb", 0) or 0)
            a[0] += ab; a[1] += hit; a[2] += wbb; a[3] += tb; a[4] += ab + wbb
    return rows


def _eval(train, test, feats):
    Xtr = np.array([[r[f] for f in feats] for r in train]); ytr = np.array([r["runs"] for r in train])
    Xte = np.array([[r[f] for f in feats] for r in test]); yte = np.array([r["runs"] for r in test])
    m = LinearRegression().fit(Xtr, ytr)
    p = m.predict(Xte)
    rmse = float(np.sqrt(np.mean((p - yte) ** 2)))
    mae = float(np.mean(np.abs(p - yte)))
    coef = dict(zip(feats, m.coef_))
    return rmse, mae, coef


def main(seasons):
    print(f"=== 타선 시너지 검증 · 시즌 {seasons} · 사전타석≥{MIN_PA} ===\n")
    rows = []
    for s in seasons:
        r = build_rows(s)
        rows += r
        print(f"  {s}: 사용 팀-경기 {len(r)}")
    rows.sort(key=lambda r: (r["date"], r["gid"], r["team"]))
    n = len(rows)
    print(f"\n총 사용 팀-경기: {n}  (평균 득점 {np.mean([r['runs'] for r in rows]):.2f})")
    if n < 400:
        print("표본 부족 — 결론 유보."); return

    # 시너지 피처가 '평균과 별개로' 득점과 상관있나 (기술통계)
    def corr(k):
        a = np.array([r[k] for r in rows]); b = np.array([r["runs"] for r in rows])
        return float(np.corrcoef(a, b)[0, 1])
    print("\n[상관] 각 피처 vs 실제 득점 (원상관, 평균 미통제)")
    for k in ["base", "syn1", "syn2", "balance"]:
        print(f"  {k:8s} r = {corr(k):+.3f}")

    # 홀드아웃: 앞 시즌 학습 → 마지막 시즌 검증
    last = seasons[-1]
    train = [r for r in rows if r["season"] != last]
    test = [r for r in rows if r["season"] == last]
    if len(train) < 200 or len(test) < 100:      # 시즌 1개뿐이면 시간 70/30
        cut = int(n * 0.7); train, test = rows[:cut], rows[cut:]
        split = "앞70%→뒤30%"
    else:
        split = f"{[s for s in seasons if s!=last]}→{last}"
    print(f"\n[홀드아웃 {split}] 득점 예측오차 (낮을수록 좋음)")
    b_rmse, b_mae, _ = _eval(train, test, ["base"])
    s_rmse, s_mae, coef = _eval(train, test, ["base", "syn1", "syn2", "balance"])
    print(f"  평균만(base)        RMSE {b_rmse:.3f}  MAE {b_mae:.3f}")
    print(f"  +시너지(syn1,2,bal) RMSE {s_rmse:.3f}  MAE {s_mae:.3f}")
    print(f"  → RMSE 개선 {b_rmse - s_rmse:+.4f}  MAE 개선 {b_mae - s_mae:+.4f}")
    print(f"    (시너지 계수: syn1 {coef['syn1']:+.2f}, syn2 {coef['syn2']:+.2f}, balance {coef['balance']:+.2f})")

    verdict = (b_rmse - s_rmse) > 0.02 or (b_mae - s_mae) > 0.02
    print(f"\n=== 판정 ===  타순 시너지 추가가치: {'O (미미하게라도 개선)' if verdict else 'X (평균이 이미 설명 — 불채택)'}")


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    main(args or [2023, 2024, 2025])
