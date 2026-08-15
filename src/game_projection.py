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
        outs = int(g["outs"].sum()); r = int(g["r"].sum())
        out[str(pcode)] = {
            "team": g.iloc[-1]["team"], "name": g.iloc[-1]["name"],
            "outs": outs, "r": r,
            "ra9": (r * 27 / outs) if outs else None,
            "dates": set(str(d) for d in g["date"]),
        }
    return out


def available_bullpen(box, team, rotation, asof, lg_ra9):
    """team의 가용 불펜 RA9(가중평균)와 결장 명단. asof=경기일(문자열).
    최근 이틀 연속 등판(=오늘 3연투) 투수는 제외."""
    starters = set(rotation["pcode"].astype(str)) if len(rotation) else set()
    ps = _pitcher_stats(box)
    d0 = datetime.date.fromisoformat(asof)
    y1 = (d0 - datetime.timedelta(days=1)).isoformat()
    y2 = (d0 - datetime.timedelta(days=2)).isoformat()
    avail, out_names = [], []
    for pcode, s in ps.items():
        if s["team"] != team or pcode in starters:
            continue
        if s["outs"] < 12:                 # 표본 너무 적은 불펜 제외
            continue
        if y1 in s["dates"] and y2 in s["dates"]:   # 이틀 연속 → 오늘 결장(3연투 방지)
            out_names.append(s["name"]); continue
        avail.append(s)
    if not avail:
        return lg_ra9, out_names
    tot_outs = sum(s["outs"] for s in avail)
    ra9 = sum((s["ra9"] or lg_ra9) * s["outs"] for s in avail) / tot_outs
    return ra9, out_names


def _game_ra9(sp_pcode, ps, bp_ra9, lg_ra9):
    """선발(SP_INN) + 불펜(BP_INN) 혼합 RA9."""
    sp = ps.get(str(sp_pcode)) if sp_pcode else None
    sp_ra9 = sp["ra9"] if (sp and sp["ra9"] and sp["outs"] >= 30) else lg_ra9
    return (SP_INN * sp_ra9 + BP_INN * bp_ra9) / (SP_INN + BP_INN)


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
        pitchH = _game_ra9(sp["home"][1], ps, bpH, lg_ra9)   # 홈이 내주는 실점력
        pitchA = _game_ra9(sp["away"][1], ps, bpA, lg_ra9)
        # 지수(리그평균=1.0)로 만들고 수축(회귀). 극단 팀을 평균 쪽으로 당김.
        def idx(v, base):
            return 1 + (v / base - 1) * SHRINK
        oH_i, oA_i = idx(rsg.get(h, lg), lg), idx(rsg.get(a, lg), lg)
        pH_i, pA_i = idx(pitchH, lg_ra9), idx(pitchA, lg_ra9)   # 실점력(높을수록 잘 내줌)
        # 기대득점: 자기 공격지수 × 상대 실점력지수 × 리그평균 × 홈보정 (log5식)
        erH = lg * oH_i * pA_i * HOME_BOOST
        erA = lg * oA_i * pH_i / HOME_BOOST
        # 승률: 단일경기 분산 반영 로지스틱(극단 압축)
        pH = 1 / (1 + math.exp(-(erH - erA) / WIN_SCALE))
        out.append({
            "date": day, "home": h, "away": a,
            "homeName": config.TEAM_NAMES.get(h, h), "awayName": config.TEAM_NAMES.get(a, a),
            "spHome": sp["home"][0], "spAway": sp["away"][0],
            "erHome": round(erH, 1), "erAway": round(erA, 1),
            "winHome": round(pH * 100), "winAway": round((1 - pH) * 100),
            "bpOutHome": outH, "bpOutAway": outA,
        })
    return {"date": day, "games": out}
