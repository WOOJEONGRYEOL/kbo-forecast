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
    ("sb", "도루", "🏃", "bat"),
    ("win", "다승", "🏆", "pit"),
    ("save", "세이브", "🔒", "pit"),
    ("hold", "홀드", "🤝", "pit"),
    ("lose", "패배", "💀", "pit"),
]
_WLS = {"승": "win", "세": "save", "홀": "hold", "패": "lose"}
TOP_N = 10                 # 표에 노출할 순위 수
SERIES_N = 8              # 레이싱 차트에 그릴 라인 수
TEAM_GAMES = 144         # KBO 정규시즌 팀당 경기 수


def _bat_path(gid):
    return Path(config.DATA_DIR) / "box_bat" / f"{gid}.json"


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

    # 팀별 소화 경기 수(페이스 계산용)
    team_gp = {}
    for g in done:
        for tc in (g.get("homeTeamCode"), g.get("awayTeamCode")):
            team_gp[tc] = team_gp.get(tc, 0) + 1

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
        # 타자 누적(홈런·안타·도루)
        bp = _bat_path(gid)
        if bp.exists():
            for r in json.loads(bp.read_text(encoding="utf-8")):
                for code in ("hr", "hit", "sb"):
                    v = int(r.get(code, 0) or 0)
                    if v:
                        bump(code, str(r["pcode"]), r.get("name", ""), r.get("team", ""), v, di)
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
