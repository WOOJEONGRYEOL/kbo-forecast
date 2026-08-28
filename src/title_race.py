# -*- coding: utf-8 -*-
"""
title_race.py — 시즌 타이틀 레이스(누적 기록 랭킹 + 페이스 + 시계열)
=====================================================================
7개 부문의 top‑N 선수를 뽑아, 현재 기록·시즌 페이스(잔여 경기 반영)와
'시즌 진행에 따른 누적값 시계열'(레이싱 차트용)을 만든다.

데이터 출처(모두 경기·날짜 단위):
  · 타자: data/box_bat/{gid}.json 캐시 (hr, hit, sb …) — boxscore가 이미 저장
  · 투수 결정: 네이버 record의 pitcher별 'wls'(승/패/세/홀) — 여기서 별도 캐시
"""
import datetime
import json
import time
from pathlib import Path

import requests

import config

# (코드, 라벨, 이모지, 종류)
RACES = [
    ("hr", "홈런", "💣", "bat"),
    ("hit", "안타", "🅗", "bat"),
    ("rbi", "타점", "🎯", "bat"),
    ("run", "득점", "⚡", "bat"),
    ("tb", "루타", "📏", "bat"),
    ("sb", "도루", "🏃", "bat"),
    ("walk", "볼넷", "👁️", "bat"),
    ("bk", "삼진", "🙈", "bat"),      # 타자 삼진(많이 당한 순 · 불명예 부문)
    ("win", "다승", "🏆", "pit"),
    ("save", "세이브", "🔒", "pit"),
    ("hold", "홀드", "🤝", "pit"),
    ("pk", "탈삼진", "🔥", "pit"),    # 투수 탈삼진
    ("inn", "이닝", "🐎", "pit"),     # 내부는 아웃카운트(3=1이닝), 표시만 이닝
    ("phr", "피홈런", "🎢", "pit"),   # 피홈런(많이 맞은 순 · 불명예 부문)
    ("lose", "패배", "💀", "pit"),
]


def _inn_to_outs(s) -> int:
    """이닝 문자열('6 ⅓' / '6 ⅔' / '6') → 아웃카운트(6⅓=19). 잘못된 값은 0."""
    s = str(s or "").strip()
    if not s:
        return 0
    thirds = 0
    if "⅓" in s:
        thirds = 1; s = s.replace("⅓", "")
    elif "⅔" in s:
        thirds = 2; s = s.replace("⅔", "")
    s = s.strip()
    try:
        return int(s) * 3 + thirds if s else thirds
    except ValueError:
        return thirds
_WLS = {"승": "win", "세": "save", "홀": "hold", "패": "lose"}
TOP_N = 10                 # 표에 노출할 순위 수
SERIES_N = 10             # 레이싱 차트에 그릴 라인 수(톱10 전부)
TEAM_GAMES = 144         # KBO 정규시즌 팀당 경기 수


def _bat_path(gid):
    return Path(config.DATA_DIR) / "box_bat" / f"{gid}.json"


def _pit_path(gid):
    return Path(config.DATA_DIR) / "box" / f"{gid}.json"


def _dec_path(gid):
    return Path(config.DATA_DIR) / "race_dec" / f"{gid}.json"


def _fetch_decisions(gid, session):
    """한 경기의 투수 결정 [{pcode,name,team,dec}]. 캐시 우선, 없으면 record에서 wls 추출."""
    p = _dec_path(gid)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    try:
        r = session.get(config.NAVER_API_BASE + f"/{gid}/record",
                        timeout=config.REQUEST_TIMEOUT_SEC,
                        headers={"User-Agent": config.USER_AGENT})
        rd = (r.json().get("result") or {}).get("recordData") or {}
    except Exception:
        return []
    gi = rd.get("gameInfo", {})
    box = rd.get("pitchersBoxscore", {}) or {}
    out = []
    for side, tk in (("home", "hCode"), ("away", "aCode")):
        for pp in box.get(side, []) or []:
            dec = _WLS.get(pp.get("wls", ""))
            if dec:
                out.append({"pcode": str(pp.get("pcode", "")), "name": pp.get("name", ""),
                            "team": gi.get(tk, ""), "dec": dec})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.2)
    return out


def build_title_race(games: list) -> dict:
    """7개 부문 레이스 데이터. 반환은 페이지가 그대로 쓰는 dict."""
    done = sorted([g for g in games if g.get("statusCode") in ("RESULT", "ENDED")
                   and not g.get("cancel")],
                  key=lambda g: (g.get("gameDate", ""), g.get("gameId", "")))
    dates = sorted({g["gameDate"] for g in done})
    didx = {d: i for i, d in enumerate(dates)}

    # 팀별 소화 경기 수(최종 페이스 계산용)
    team_gp = {}
    for g in done:
        for tc in (g.get("homeTeamCode"), g.get("awayTeamCode")):
            team_gp[tc] = team_gp.get(tc, 0) + 1

    # 날짜별 팀 소화경기 누적(재생 중 동적 페이스용)
    team_day = {}
    for g in done:
        di = didx[g["gameDate"]]
        for tc in (g.get("homeTeamCode"), g.get("awayTeamCode")):
            team_day.setdefault(tc, [0] * len(dates))[di] += 1
    team_cum = {}
    for tc, arr in team_day.items():
        acc = 0; run = [0] * len(dates)
        for i, c in enumerate(arr):
            acc += c; run[i] = acc
        team_cum[tc] = run

    codes = [c for c, *_ in RACES]
    cum = {c: {} for c in codes}          # code -> pcode -> 누적값
    meta = {}                             # pcode -> {name, team}
    series = {c: {} for c in codes}       # code -> pcode -> {date_idx: 누적값}

    def bump(code, pc, name, team, add, di):
        cum[code][pc] = cum[code].get(pc, 0) + add
        meta[pc] = {"name": name, "team": team}
        series[code].setdefault(pc, {})[di] = cum[code][pc]

    sess = requests.Session()
    for g in done:
        gid = g["gameId"]; di = didx[g["gameDate"]]
        # 타자 누적(홈런·안타·도루 + 삼진 bk)
        bp = _bat_path(gid)
        if bp.exists():
            for r in json.loads(bp.read_text(encoding="utf-8")):
                for src, code in (("hr", "hr"), ("hit", "hit"), ("rbi", "rbi"),
                                  ("run", "run"), ("tb", "tb"), ("sb", "sb"),
                                  ("bb", "walk"), ("kk", "bk")):
                    v = int(r.get(src, 0) or 0)
                    if v:
                        bump(code, str(r["pcode"]), r.get("name", ""), r.get("team", ""), v, di)
        # 투수 탈삼진 누적(pk) — 박스스코어 캐시
        pp = _pit_path(gid)
        if pp.exists():
            for r in json.loads(pp.read_text(encoding="utf-8")):
                pc, nm, tm = str(r["pcode"]), r.get("name", ""), r.get("team", "")
                kk = int(r.get("kk", 0) or 0)
                if kk:
                    bump("pk", pc, nm, tm, kk, di)
                phr = int(r.get("hr", 0) or 0)
                if phr:
                    bump("phr", pc, nm, tm, phr, di)
                outs = _inn_to_outs(r.get("inn"))
                if outs:
                    bump("inn", pc, nm, tm, outs, di)
        # 투수 결정 누적(다승·세이브·홀드·패배)
        for d in _fetch_decisions(gid, sess):
            bump(d["dec"], d["pcode"], d["name"], d["team"], 1, di)

    def carry(pc_series):
        """{date_idx: val} → dates 전체에 대해 carry-forward 배열(첫 등장 전은 null)."""
        arr = [None] * len(dates)
        last = None
        for i in range(len(dates)):
            if i in pc_series:
                last = pc_series[i]
            arr[i] = last
        return arr

    def pace_carry(pc_series, tc):
        """그 시점 누적기록 × 144 ÷ 그때까지 팀 소화경기 → 날짜별 페이스 배열."""
        val = carry(pc_series)
        tcum = team_cum.get(tc)
        out = [None] * len(dates)
        for i in range(len(dates)):
            v = val[i]
            if v is None:
                continue
            gp = tcum[i] if tcum else 0
            out[i] = round(v * TEAM_GAMES / gp) if gp else v
        return out

    races = {}
    for code, label, emoji, kind in RACES:
        ranked = sorted(cum[code].items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]
        leaders = []
        for rank, (pc, total) in enumerate(ranked, 1):
            m = meta.get(pc, {})
            tc = m.get("team", "")
            gp = team_gp.get(tc, 0)
            pace = round(total / gp * TEAM_GAMES) if gp else total
            row = {"rank": rank, "pcode": pc, "name": m.get("name", ""), "team": tc,
                   "total": total, "pace": pace}
            if rank <= SERIES_N:
                row["series"] = carry(series[code].get(pc, {}))
                row["paceSeries"] = pace_carry(series[code].get(pc, {}), tc)
            leaders.append(row)
        races[code] = {"label": label, "emoji": emoji, "kind": kind, "leaders": leaders}

    return {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "dates": dates, "teamGames": TEAM_GAMES, "races": races,
            "order": codes}


def save_title_race_json(games, path: str = None) -> str:
    path = path or f"{config.DATA_DIR}/title_race.json"
    data = build_title_race(games)
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path
