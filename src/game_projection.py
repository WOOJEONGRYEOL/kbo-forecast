# -*- coding: utf-8 -*-
"""
game_projection.py — 당일 경기 '기대 스코어' 모델
====================================================

예고선발(preview, 전날) + 팀 공격력(RS/G) + 상대 선발·가용 불펜(3연투 제외)로
경기 기대득점/기대실점과 승리확률을 추정한다.

⚠️ 정직한 한계: 단일 경기 승부 예측의 정직한 천장은 ~56%(experiments/ 참조).
   이 카드의 값어치는 '맞히기'가 아니라 기대 스코어·불펜 리스크의 서술에 있다.
   라인업(타순)은 경기 ~1시간 전 발표라 빌드 시점엔 없으므로 팀 공격력으로 근사한다.
"""
import datetime
import math
from collections import defaultdict

import requests

import config
from boxscore import _innings_to_outs, identify_rotation

SP_INN, BP_INN = 5.1, 3.9   # 경기당 선발/불펜 담당 이닝 근사
HOME_BOOST = 1.035       # 홈 어드밴티지(팀별 스플릿은 과적합 → 상수 사용)
SHRINK = 0.85            # 팀 지표를 리그평균으로 당기는 수축(시즌 표본 추정오차·회귀 반영)
WIN_SCALE = 5.5          # 승률 로지스틱 스케일(단일경기는 분산이 커 극단 압축; 큰 격차도 ~70%)
PREVIEW_URL = config.NAVER_API_BASE + "/{gid}/preview"


def _get(url):
    r = requests.get(url, timeout=config.REQUEST_TIMEOUT_SEC,
                     headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    return r.json()


def probable_starters(game_id: str) -> dict:
    """preview → {'home': (name, pCode), 'away': (name, pCode)}. 없으면 (None, None)."""
    try:
        pv = (_get(PREVIEW_URL.format(gid=game_id)).get("result") or {}).get("previewData") or {}
    except Exception:
        return {"home": (None, None), "away": (None, None)}
    out = {}
    for side, key in (("home", "homeStarter"), ("away", "awayStarter")):
        pi = (pv.get(key) or {}).get("playerInfo") or {}
        out[side] = (pi.get("name"), str(pi.get("pCode")) if pi.get("pCode") else None)
    return out


def team_offense(games: list) -> tuple:
    """팀별 시즌 득점/경기(RS/G)와 리그 평균. 반환 (rsg{team}, lg_rpg)."""
    rs, gp = defaultdict(float), defaultdict(int)
    n = 0
    for g in games:
        if g.get("statusCode") != "RESULT" or g.get("cancel"):
            continue
        h, a = g.get("homeTeamCode"), g.get("awayTeamCode")
        hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
        if None in (h, a, hs, as_):
            continue
        rs[h] += hs; gp[h] += 1
        rs[a] += as_; gp[a] += 1
        n += 1
    rsg = {t: rs[t] / gp[t] for t in rs if gp[t]}
    lg = (sum(rs.values()) / sum(gp.values())) if gp else 4.8
    return rsg, lg


def _pitcher_stats(box):
    """pcode → {outs, r, ra9, last_dates:set}. box는 collect_season_pitching 결과."""
    df = box.copy()
    df["outs"] = df["inn"].map(_innings_to_outs)
    out = {}
    for pcode, g in df.groupby("pcode"):
        g = g.sort_values("date")
        outs = int(g["outs"].sum()); r = int(g["r"].sum())
        starts = g[g["outs"] >= 9]              # 3이닝+ 등판 = 선발 근사
        # 등판별 추정 투구수 = (아웃+피안타+볼넷사구) × 3.9  (API에 실투구수 없어 근사)
        apps = [{"date": str(row["date"]),
                 "pit": round((int(row["outs"]) + int(row.get("hit", 0))
                               + int(row.get("bbhp", 0))) * 3.9)}
                for _, row in g.iterrows()]
        out[str(pcode)] = {
            "team": g.iloc[-1]["team"], "name": g.iloc[-1]["name"],
            "outs": outs, "r": r,
            "ra9": (r * 27 / outs) if outs else None,
            "start_outs_avg": float(starts["outs"].mean()) if len(starts) >= 3 else None,
            "apps": apps,
            "dates": set(a["date"] for a in apps),
        }
    return out


def _rest_days(pit):
    """전날(최근) 투구수 → 필요 휴식일. 30↓=0, ~45=1, ~60=2, ~75=3, 초과=4."""
    if pit <= 30:
        return 0
    if pit <= 45:
        return 1
    if pit <= 60:
        return 2
    if pit <= 75:
        return 3
    return 4


def available_bullpen(box, team, rotation, asof, lg_ra9, ps=None):
    """team의 가용 불펜 RA9(가중평균)와 결장 사유. asof=경기일(문자열).
    결장 규칙:
      · 3연투: 전날까지 3일 연속 등판 → 당일 결장
      · 투구수 휴식: 최근 등판 추정투구수 30~45=1일, 45~60=2일, 60~75=3일 결장
    (투구수는 API에 없어 상대타자수×3.9로 추정)"""
    starters = set(rotation["pcode"].astype(str)) if len(rotation) else set()
    ps = ps or _pitcher_stats(box)
    d0 = datetime.date.fromisoformat(asof)
    avail, out_info = [], []
    for pcode, s in ps.items():
        if s["team"] != team or pcode in starters or s["outs"] < 12:
            continue
        dates = s["dates"]
        three = all((d0 - datetime.timedelta(days=k)).isoformat() in dates for k in (1, 2, 3))
        last = s["apps"][-1]
        days_since = (d0 - datetime.date.fromisoformat(last["date"])).days
        rest = _rest_days(last["pit"])
        if three:
            out_info.append({"name": s["name"], "reason": "3연투"}); continue
        if 0 < days_since <= rest:
            out_info.append({"name": s["name"], "reason": f"{last['pit']}구·{rest}일휴식"}); continue
        avail.append(s)
    if not avail:
        return lg_ra9, out_info
    tot = sum(s["outs"] for s in avail)
    ra9 = sum((s["ra9"] or lg_ra9) * s["outs"] for s in avail) / tot
    return ra9, out_info


def _game_ra9(sp_pcode, ps, bp_ra9, lg_ra9):
    """선발(평균 소화이닝) + 불펜(9−선발이닝) 혼합 RA9.
    반환 (혼합, 선발RA9, 선발기록여부, 선발이닝)."""
    sp = ps.get(str(sp_pcode)) if sp_pcode else None
    known = bool(sp and sp["ra9"] and sp["outs"] >= 30)
    sp_ra9 = sp["ra9"] if known else lg_ra9
    # 선발별 평균 소화이닝 반영(있으면), 없으면 기본값. 3.5~7.0이닝으로 제한.
    if sp and sp.get("start_outs_avg"):
        sp_inn = max(3.5, min(7.0, sp["start_outs_avg"] / 3))
    else:
        sp_inn = SP_INN
    bp_inn = 9 - sp_inn
    blended = (sp_inn * sp_ra9 + bp_inn * bp_ra9) / 9
    return blended, sp_ra9, known, round(sp_inn, 1)


def project_games(games: list, box, ref_date: str = None) -> dict:
    """오늘(없으면 다음 예정일) 경기들의 기대 스코어·승률. 반환 {date, games:[...]}."""
    today = ref_date or datetime.date.today().isoformat()
    sched = [g for g in games if g.get("statusCode") == "BEFORE" and not g.get("cancel")]
    day = today if any(g["gameDate"] == today for g in sched) else (
        min((g["gameDate"] for g in sched), default=None))
    if day is None:
        return {"date": None, "games": []}
    todays = [g for g in games if g.get("gameDate") == day and not g.get("cancel")]

    rsg, lg = team_offense(games)
    lg_ra9 = lg                                   # 리그 평균 실점/9 ≈ 득점/9
    ps = _pitcher_stats(box)
    rotation = identify_rotation(box)

    out = []
    for g in todays:
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        sp = probable_starters(g["gameId"])
        bpH, outH = available_bullpen(box, h, rotation, day, lg_ra9)
        bpA, outA = available_bullpen(box, a, rotation, day, lg_ra9)
        # 상대 이 경기 실점력(선발+가용불펜)
        pitchH, spH_ra9, spH_known, spH_inn = _game_ra9(sp["home"][1], ps, bpH, lg_ra9)
        pitchA, spA_ra9, spA_known, spA_inn = _game_ra9(sp["away"][1], ps, bpA, lg_ra9)
        oH, oA = rsg.get(h, lg), rsg.get(a, lg)
        # 지수(리그평균=1.0)로 만들고 수축(회귀). 극단 팀을 평균 쪽으로 당김.
        def idx(v, base):
            return 1 + (v / base - 1) * SHRINK
        oH_i, oA_i = idx(oH, lg), idx(oA, lg)
        pH_i, pA_i = idx(pitchH, lg_ra9), idx(pitchA, lg_ra9)   # 실점력(높을수록 잘 내줌)
        # 기대득점: 자기 공격지수 × 상대 실점력지수 × 리그평균 × 홈보정 (log5식)
        erH = lg * oH_i * pA_i * HOME_BOOST
        erA = lg * oA_i * pH_i / HOME_BOOST
        # 승률: 단일경기 분산 반영 로지스틱(극단 압축)
        pH = 1 / (1 + math.exp(-(erH - erA) / WIN_SCALE))
        r2 = lambda v: round(v, 2)
        out.append({
            "date": day, "home": h, "away": a,
            "homeName": config.TEAM_NAMES.get(h, h), "awayName": config.TEAM_NAMES.get(a, a),
            "spHome": sp["home"][0], "spAway": sp["away"][0],
            "erHome": round(erH, 1), "erAway": round(erA, 1),
            "winHome": round(pH * 100), "winAway": round((1 - pH) * 100),
            "bpOutHome": outH, "bpOutAway": outA,
            "calc": {
                "lg": r2(lg), "boost": HOME_BOOST,
                "offHome": r2(oH), "offAway": r2(oA), "oIdxHome": r2(oH_i), "oIdxAway": r2(oA_i),
                "spHomeRa9": r2(spH_ra9), "spHomeKnown": spH_known, "spHomeInn": spH_inn,
                "bpHome": r2(bpH), "bpHomeInn": r2(9 - spH_inn),
                "pitchHome": r2(pitchH), "pIdxHome": r2(pH_i),
                "spAwayRa9": r2(spA_ra9), "spAwayKnown": spA_known, "spAwayInn": spA_inn,
                "bpAway": r2(bpA), "bpAwayInn": r2(9 - spA_inn),
                "pitchAway": r2(pitchA), "pIdxAway": r2(pA_i),
            },
        })
    return {"date": day, "games": out}
