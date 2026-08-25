# -*- coding: utf-8 -*-
"""
불펜 누적 피로 실증 검증 (2026 박스스코어)
==========================================
질문: 구원투수가 최근 1~2일에 많이 던졌으면(누적 부하), 오늘 등판에서 실제로 더 실점하나?

방법: 투수-등판 단위(선발 등판 제외).
  · recent_load = 이 등판 직전 1~2일에 던진 투구수 합(bf)
  · 성과 = 이 등판의 '아웃당 실점'(r/outs)
  · 투수 실력 교란 제거: 같은 투수의 '쉰 등판 vs 지친 등판'을 짝지어 비교
    (each 투수의 시즌 평균 아웃당 실점 대비 잔차)
"""
import sys
import json
import glob
from collections import defaultdict
from datetime import date

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import naver_games  # noqa: E402
import boxscore  # noqa: E402
import config  # noqa: E402


def mean_se(xs):
    n = len(xs)
    if n == 0:
        return (float("nan"), float("nan"), 0)
    m = sum(xs) / n
    if n < 2:
        return (m, float("nan"), n)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return (m, (var / n) ** 0.5, n)


def sig(m, se):
    if se != se or se == 0:
        return ""
    z = m / se
    return "  ***" if abs(z) >= 2.58 else "  **" if abs(z) >= 1.96 else "  ·" if abs(z) >= 1.64 else ""


games = naver_games.filter_official_teams(
    naver_games.filter_regular_season(naver_games.fetch_season_games(config.SEASON)))
box = boxscore.collect_season_pitching(games)
box = box.copy()
box["outs"] = box["inn"].map(boxscore._innings_to_outs)

# 그 경기 첫 투수 = 선발 → 구원 등판만 남김
firsts = box.drop_duplicates(["game_id", "team"], keep="first")
starter_pairs = set(zip(firsts["game_id"].astype(str), firsts["pcode"].astype(str)))

# 투수별 등판 목록
apps = defaultdict(list)   # pcode -> [{date, pit, outs, r, er, started}]
for _, row in box.iterrows():
    pc = str(row["pcode"])
    apps[pc].append({
        "date": str(row["date"]), "pit": int(row.get("bf", 0) or 0),
        "outs": int(row["outs"]), "r": int(row.get("r", 0) or 0),
        "er": int(row.get("er", 0) or 0),
        "started": (str(row["game_id"]), pc) in starter_pairs,
    })

records = []   # 구원 등판: {pcode, load, rpo, resid, gave_run}
per_pitcher = defaultdict(list)
for pc, lst in apps.items():
    lst.sort(key=lambda a: a["date"])
    relief = [a for a in lst if not a["started"] and a["outs"] >= 1]
    if len(relief) < 5:
        continue
    tot_r = sum(a["r"] for a in relief); tot_o = sum(a["outs"] for a in relief)
    base = tot_r / tot_o if tot_o else 0        # 이 투수의 시즌 아웃당 실점
    dates = {a["date"]: a for a in lst}
    for a in relief:
        d0 = date.fromisoformat(a["date"])
        load = sum(x["pit"] for x in lst
                   if 1 <= (d0 - date.fromisoformat(x["date"])).days <= 2)
        rpo = a["r"] / a["outs"]
        rec = {"pcode": pc, "load": load, "rpo": rpo, "resid": rpo - base,
               "gave_run": 1 if a["r"] > 0 else 0, "base": base}
        records.append(rec); per_pitcher[pc].append(rec)

print(f"=== 표본: {len(records)} 구원 등판 · 투수 {len(per_pitcher)}명 ({config.SEASON}) ===")
print(f"평균 잔차(전체, 0이어야 정상): {mean_se([r['resid'] for r in records])[0]:+.4f}")
print(f"전체 평균 아웃당 실점: {mean_se([r['rpo'] for r in records])[0]:.3f}\n")


def bucket(load):
    return "0(완전휴식)" if load == 0 else "1~29구" if load < 30 else "30~49구" if load < 50 else "50구+"


order = ["0(완전휴식)", "1~29구", "30~49구", "50구+"]
g = defaultdict(list)
for r in records:
    g[bucket(r["load"])].append(r)

print("── 최근 1~2일 투구 부하별 성과 ──")
print(f"{'부하 구간':<14}{'등판':>6}{'아웃당실점 잔차':>16}{'실점경기 비율':>14}")
for k in order:
    rl = g.get(k, [])
    if not rl:
        continue
    m, se, n = mean_se([r["resid"] for r in rl])
    gr = sum(r["gave_run"] for r in rl) / n
    print(f"{k:<14}{n:>6}{m:>+10.4f} ±{se:.4f}{gr*100:>11.1f}%{sig(m, se)}")

# 같은 투수 안에서 '지친 등판(load>=30) vs 완전휴식(load=0)' 짝 비교
print("\n── 같은 투수 짝비교: 지친 등판(≥30구) − 완전휴식(0구), 아웃당 실점 차 ──")
diffs = []
for pc, rl in per_pitcher.items():
    tired = [r["rpo"] for r in rl if r["load"] >= 30]
    rested = [r["rpo"] for r in rl if r["load"] == 0]
    if len(tired) >= 2 and len(rested) >= 2:
        diffs.append(sum(tired) / len(tired) - sum(rested) / len(rested))
m, se, n = mean_se(diffs)
print(f"투수 {n}명 평균 차이: {m:+.4f} ±{se:.4f} 아웃당 실점{sig(m, se)}")
print(f"  (양수·유의하면 '지치면 더 내줌' — 우리 모델 방향과 일치. 아웃당 {m:+.4f} ≈ 9이닝당 {m*27:+.2f}실점)")

# 연속등판(3연투 직전 등)도 참고: load 상관
xs = [r["load"] for r in records]; ys = [r["resid"] for r in records]
mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
sx = (sum((x - mx) ** 2 for x in xs) / len(xs)) ** 0.5
sy = (sum((y - my) ** 2 for y in ys) / len(ys)) ** 0.5
corr = cov / (sx * sy) if sx and sy else 0
print(f"\n부하↔잔차 상관 r={corr:+.3f} (n={len(records)})")
print("\n※ ***=99% · **=95% · ·=90% 유의.")
