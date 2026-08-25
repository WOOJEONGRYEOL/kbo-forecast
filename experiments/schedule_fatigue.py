# -*- coding: utf-8 -*-
"""
일정 피로(휴식일·연속 원정·이동 거리) 실증 검증
================================================
질문: 팀이 (a)덜 쉬거나 (b)긴 원정 연전 중이거나 (c)먼 거리를 이동했을 때,
      실제로 기대(팀 실력·상대·홈/원정 통제)보다 덜 득점하고 덜 이기는가?

방법: 여러 시즌 경기 로그 → 팀-경기 단위로
  · 기대 득점 exp = 리그평균 × (팀 시즌 RS/G / 리그) × (상대 시즌 RA/G / 리그) × 홈보정
  · 잔차 resid = 실제 득점 − exp   (팀 실력·상대·홈/원정을 통제한 '초과 득점')
  일정 상태별로 resid·승률을 비교. 실력 교란을 더 줄이려 '원정 연전 위치'(같은 팀
  안에서 초반 vs 후반)도 따로 본다.
"""
import sys
import math
from collections import defaultdict
from datetime import date

sys.path.insert(0, "src"); sys.path.insert(0, ".")
import naver_games  # noqa: E402

SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]
HOME_ADV = 1.03   # 홈 득점 보정(대략)

STAD = {
    "잠실": (37.512, 127.072), "고척": (37.498, 126.867), "문학": (37.437, 126.693),
    "수원": (37.300, 127.010), "대전": (36.317, 127.429), "대구": (35.841, 128.681),
    "사직": (35.194, 129.061), "창원": (35.222, 128.582), "광주": (35.168, 126.889),
    "포항": (36.008, 129.359), "울산": (35.532, 129.266), "청주": (36.640, 127.470),
    "마산": (35.222, 128.582),
}


def hav(a, b):
    r = 6371.0
    dlat = math.radians(b[0] - a[0]); dlon = math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0]))
         * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


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


rows = []   # 팀-경기: {season, team, home, rest, road, travel, rf, exp, resid, win}
for season in SEASONS:
    try:
        games = naver_games.filter_official_teams(
            naver_games.filter_regular_season(naver_games.fetch_season_games(season)))
    except Exception as e:
        print(f"  {season} 시즌 로드 실패: {e}"); continue
    done = [g for g in games if g.get("statusCode") in ("RESULT", "ENDED")
            and not g.get("cancel") and g.get("homeTeamScore") is not None
            and g.get("awayTeamScore") is not None]
    if not done:
        continue
    sch = defaultdict(list)
    for g in sorted(done, key=lambda x: (x.get("gameDate", ""), x.get("gameDateTime", ""))):
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        st = g.get("stadium")
        sch[h].append((g["gameDate"], True, st, g["homeTeamScore"], g["awayTeamScore"], a))
        sch[a].append((g["gameDate"], False, st, g["awayTeamScore"], g["homeTeamScore"], h))
    rs = defaultdict(float); ra = defaultdict(float); gp = defaultdict(int)
    for t, lst in sch.items():
        for (_, _, _, rf, rag, _) in lst:
            rs[t] += rf; ra[t] += rag; gp[t] += 1
    lg = sum(rs.values()) / sum(gp.values())
    rsg = {t: rs[t] / gp[t] for t in rs}; rag = {t: ra[t] / gp[t] for t in ra}
    for t, lst in sch.items():
        for i, (d, ih, st, rf, rag_g, opp) in enumerate(lst):
            if i == 0:
                continue
            pd_, _, pst = lst[i - 1][0], lst[i - 1][1], lst[i - 1][2]
            try:
                rest = (date.fromisoformat(d) - date.fromisoformat(pd_)).days
            except Exception:
                continue
            road = 0
            for j in range(i, -1, -1):
                if lst[j][1] is False:
                    road += 1
                else:
                    break
            travel = 0.0
            if rest <= 1 and pst and st and pst != st and pst in STAD and st in STAD:
                travel = hav(STAD[pst], STAD[st])
            exp = lg * (rsg[t] / lg) * (rag[opp] / lg) * (HOME_ADV if ih else 1 / HOME_ADV)
            rows.append({"season": season, "team": t, "home": ih, "rest": rest,
                         "road": road, "travel": travel, "rf": rf, "exp": exp,
                         "resid": rf - exp, "win": 1 if rf > rag_g else 0})

print(f"=== 표본: {len(rows)} 팀-경기 ({SEASONS[0]}~{SEASONS[-1]}) ===")
print(f"평균 잔차(전체, 0이어야 정상): {mean_se([r['resid'] for r in rows])[0]:+.3f}\n")


def bucket_report(title, subset, keyfn, order):
    print(f"── {title} (n={len(subset)}) ──")
    print(f"{'구간':<14}{'경기':>6}{'잔차(초과득점)':>16}{'승률':>10}")
    g = defaultdict(list)
    for r in subset:
        g[keyfn(r)].append(r)
    base = None
    for k in order:
        rl = g.get(k, [])
        if not rl:
            print(f"{k:<14}{0:>6}{'—':>16}"); continue
        m, se, n = mean_se([r["resid"] for r in rl])
        w = sum(r["win"] for r in rl) / n
        if base is None:
            base = m
        print(f"{k:<14}{n:>6}{m:>+10.3f} ±{se:.3f}{w*100:>8.1f}%{sig(m, se)}")
    print()


# 1) 휴식일 (홈/원정 각각)
for side, lab in [(True, "홈"), (False, "원정")]:
    sub = [r for r in rows if r["home"] is side and r["rest"] is not None]
    bucket_report(f"휴식일 효과 — {lab} 경기",
                  sub, lambda r: "1일" if r["rest"] == 1 else "2일" if r["rest"] == 2 else "3일+" if r["rest"] >= 3 else "0일",
                  ["0일", "1일", "2일", "3일+"])

# 2) 연속 원정 (원정 경기만; 같은 팀 안 초반 vs 후반이라 실력 교란 적음)
sub = [r for r in rows if r["home"] is False]
bucket_report("연속 원정 길이 효과 — 원정 경기(원정 연전 N번째)",
              sub, lambda r: "1~2연전" if r["road"] <= 2 else "3~4연전" if r["road"] <= 4 else "5~6연전" if r["road"] <= 6 else "7연전+",
              ["1~2연전", "3~4연전", "5~6연전", "7연전+"])

# 3) 이동 거리 (전날 경기 + 구장 변경)
sub = [r for r in rows if r["travel"] > 0]
bucket_report("이동 거리 효과 — 전날 경기 후 구장 이동",
              sub, lambda r: "~100km" if r["travel"] < 100 else "100~200km" if r["travel"] < 200 else "200km+",
              ["~100km", "100~200km", "200km+"])
# 이동 없음(같은 구장 연속, rest<=1) 대조군
same = [r for r in rows if r["travel"] == 0 and r["rest"] is not None and r["rest"] <= 1 and r["home"] is False]
m, se, n = mean_se([r["resid"] for r in same])
print(f"대조군(원정 같은구장 연속, 이동0): n={n} 잔차 {m:+.3f} ±{se:.3f} 승률 {sum(r['win'] for r in same)/n*100:.1f}%\n")

# 4) 이동거리 vs 잔차 상관(연속형)
tv = [r for r in rows if r["travel"] > 0]
if len(tv) > 10:
    xs = [r["travel"] for r in tv]; ys = [r["resid"] for r in tv]
    mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    sx = (sum((x - mx) ** 2 for x in xs) / len(xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / len(ys)) ** 0.5
    corr = cov / (sx * sy) if sx and sy else 0
    slope = cov / (sx * sx) if sx else 0
    print(f"이동거리↔잔차 상관 r={corr:+.3f}, 기울기 {slope*100:+.4f}점/100km (n={len(tv)})")
    print("  (음수·유의하면 '멀수록 덜 득점' — 우리 모델 방향과 일치)")

print("\n※ ***=99% · **=95% · ·=90% 유의(잔차가 0과 다름). 표시 없으면 통계적으로 0과 구분 안 됨.")
