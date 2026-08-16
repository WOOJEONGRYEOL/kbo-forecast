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


def preview_data(game_id: str) -> dict:
    """게임 preview의 previewData(선발·라인업 포함). 실패 시 {}."""
    try:
        return (_get(PREVIEW_URL.format(gid=game_id)).get("result") or {}).get("previewData") or {}
    except Exception:
        return {}


def probable_starters(game_id: str, pv: dict = None) -> dict:
    """preview → {'home': (name, pCode), 'away': (name, pCode)}. 없으면 (None, None)."""
    pv = pv if pv is not None else preview_data(game_id)
    out = {}
    for side, key in (("home", "homeStarter"), ("away", "awayStarter")):
        pi = (pv.get(key) or {}).get("playerInfo") or {}
        out[side] = (pi.get("name"), str(pi.get("pCode")) if pi.get("pCode") else None)
    return out


# 타순별 경기당 타석(PA) 근사 가중 — 1번타자가 가장 많이 친다.
_BATORDER_PA = [4.65, 4.55, 4.45, 4.35, 4.25, 4.12, 4.00, 3.90, 3.80]


def lineup_from_preview(pv: dict, side: str) -> list:
    """발표된 타격 라인업 → [(pcode, name)] 타순 순서(선발투수 제외). 미발표면 []."""
    key = "homeTeamLineUp" if side == "home" else "awayTeamLineUp"
    fl = (pv.get(key) or {}).get("fullLineUp") or []
    batters = [x for x in fl if x.get("positionName") != "선발투수"]
    if len(batters) < 9:
        return []
    return [(str(x.get("playerCode")), x.get("playerName")) for x in batters[:9]]


def load_lineup_wrc(season: int = None):
    """최신 batters_*.csv → (wrc_by_pcode, team_base). team_base=팀 상위9(PA) PA가중 wRC+.
    네트워크 없이 로컬 캐시만 사용(빈번한 today 빌드용). 없으면 ({}, {})."""
    import glob
    import pandas as pd
    files = sorted(glob.glob(f"{config.DATA_DIR}/batters_*.csv"))
    if not files:
        return {}, {}
    df = pd.read_csv(files[-1]).dropna(subset=["pcode", "wrc_plus_pure", "team_code"])
    df["pcode"] = df["pcode"].astype(int).astype(str)
    wrc_by_p = dict(zip(df["pcode"], df["wrc_plus_pure"].astype(float)))
    base = {}
    for tc, g in df.groupby("team_code"):
        g = g.sort_values("n_pa", ascending=False).head(9)   # 주전 9명 근사
        w = g["n_pa"].clip(lower=1)
        base[str(tc)] = float((g["wrc_plus_pure"] * w).sum() / w.sum())
    return wrc_by_p, base


def lineup_multiplier(batters: list, wrc_by_p: dict, team_base: float):
    """타순 순서 [(pcode,name)] → (공격배수, [(name, wrc)…]). 미발표/데이터없으면 (1.0, []).
    배수 = 오늘 라인업 타순가중 wRC+ ÷ 팀 상위9 베이스 wRC+ (0.85~1.15 제한)."""
    if not batters or not team_base:
        return 1.0, []
    vals, detail, wsum = 0.0, [], 0.0
    for i, (pc, name) in enumerate(batters):
        w = _BATORDER_PA[i] if i < len(_BATORDER_PA) else 3.8
        wrc = wrc_by_p.get(pc)
        if wrc is None:            # 무기록(신인·콜업) → 팀 베이스로 대체
            wrc = team_base
        vals += w * wrc; wsum += w
        detail.append((name, round(wrc)))
    lu_wrc = vals / wsum if wsum else team_base
    mult = max(0.85, min(1.15, lu_wrc / team_base))
    return mult, detail


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


def park_factors(games: list) -> dict:
    """구장별 득점 환경 팩터 = (구장 경기당 총득점)/(리그 경기당 총득점).
    한 시즌은 표본이 작아 1.0으로 50% 수축. >1=타자친화, <1=투수친화."""
    tot = defaultdict(lambda: [0.0, 0])
    lg_r, lg_g = 0.0, 0
    for g in games:
        if g.get("statusCode") != "RESULT" or g.get("cancel"):
            continue
        hs, as_, st = g.get("homeTeamScore"), g.get("awayTeamScore"), g.get("stadium")
        if None in (hs, as_) or not st:
            continue
        tot[st][0] += hs + as_; tot[st][1] += 1
        lg_r += hs + as_; lg_g += 1
    lg = lg_r / lg_g if lg_g else 9.6
    out = {}
    for st, (r, n) in tot.items():
        out[st] = 1.0 if n < 20 else round(1 + ((r / n) / lg - 1) * 0.5, 3)
    return out


def _band(mu: float):
    """단일 경기 득점의 대략적 범위(과분산 근사, ~50% 중심구간)."""
    sd = math.sqrt(mu + mu * mu / 6)      # Var ≈ μ + μ²/6 (음이항 근사)
    return [max(0, round(mu - 0.7 * sd)), round(mu + 0.7 * sd)]


def _pitcher_stats(box):
    """pcode → {outs, r, ra9, last_dates:set}. box는 collect_season_pitching 결과."""
    df = box.copy()
    df["outs"] = df["inn"].map(_innings_to_outs)
    # 그 경기의 '첫 투수' = 실제 선발 (box는 등판 순서 보존). 이 (경기,pcode) 쌍으로
    # 등판별 선발 여부를 정확히 판정 — 3이닝 롱릴리프를 선발로 오인하지 않음.
    firsts = df.drop_duplicates(["game_id", "team"], keep="first")
    starter_pairs = set(zip(firsts["game_id"].astype(str), firsts["pcode"].astype(str)))
    out = {}
    for pcode, g in df.groupby("pcode"):
        g = g.sort_values("date")
        outs = int(g["outs"].sum()); r = int(g["r"].sum())
        starts = g[g["outs"] >= 9]              # 3이닝+ 등판 = 선발 근사(평균이닝용)
        # 등판별 실제 투구수: box의 'bf' 필드가 네이버 per-game 투구수(상대타자수는 pa).
        apps = [{"date": str(row["date"]), "pit": int(row.get("bf", 0) or 0),
                 "outs": int(row["outs"]),
                 "started": (str(row["game_id"]), str(pcode)) in starter_pairs}
                for _, row in g.iterrows()]
        # 현재 역할 판정(누적 선발수가 아니라 '최근 역할'):
        #   · 가장 최근 등판이 선발이면 → 로테이션 중(복귀·스팟 선발 1번도 포함)
        #   · 또는 최근 5등판 중 선발이 2회 이상이면 → 아직 선발
        #   둘 다 아니면(최근이 전부 계투) 불펜으로 보고 풀에 포함(예: 두산 다카다).
        recent = apps[-5:]
        recent_starts = sum(1 for a in recent if a["started"])
        last_started = bool(apps) and apps[-1]["started"]
        is_starter_now = last_started or recent_starts >= 2
        out[str(pcode)] = {
            "team": g.iloc[-1]["team"], "name": g.iloc[-1]["name"],
            "outs": outs, "r": r,
            "ra9": (r * 27 / outs) if outs else None,
            "start_outs_avg": float(starts["outs"].mean()) if len(starts) >= 3 else None,
            "is_starter_now": is_starter_now,
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
    if pit < 75:
        return 3
    return 4       # 75구 이상 → 4일


def available_bullpen(box, team, rotation, asof, lg_ra9, ps=None, exclude=None):
    """team의 가용 불펜 RA9(가중평균)와 결장 사유. asof=경기일(문자열).
    '불펜'은 시즌 누적 선발수가 아니라 최근 역할(is_starter_now)로 판정 —
    시즌 초 몇 번 선발한 뒤 지금은 불펜인 투수도 풀에 포함한다.
    exclude=오늘 예고선발 등 명시적으로 뺄 pcode 집합.
    결장 규칙:
      · 3연투: 전날까지 3일 연속 등판 → 당일 결장
      · 투구수 휴식: 최근 등판 투구수 30~45=1일, 45~60=2일, 60~75=3일 결장
    (투구수는 네이버 박스스코어 실값 'bf' 사용)"""
    exclude = set(str(x) for x in (exclude or []) if x)
    ps = ps or _pitcher_stats(box)
    d0 = datetime.date.fromisoformat(asof)
    avail, out_info = [], []
    for pcode, s in ps.items():
        if (s["team"] != team or pcode in exclude
                or s.get("is_starter_now") or s["outs"] < 12):
            continue
        dates = s["dates"]
        # 3연투: 전날까지 3일 연속 등판
        if all((d0 - datetime.timedelta(days=k)).isoformat() in dates for k in (1, 2, 3)):
            out_info.append({"name": s["name"], "reason": "3연투"}); continue
        # 투구수 휴식: 최근 4일 등판을 각각 확인 — 그 등판의 휴식창(등판+N일)이
        # 오늘을 덮으면 결장. (며칠 전 대량 투구가 아직 안 풀린 경우도 포착)
        binders = [(a, k) for a in s["apps"]
                   for k in [(d0 - datetime.date.fromisoformat(a["date"])).days]
                   if 1 <= k <= 4 and k <= _rest_days(a["pit"])]
        if binders:
            a, k = max(binders, key=lambda x: _rest_days(x[0]["pit"]))
            rest = _rest_days(a["pit"])
            tag = "전날" if k == 1 else f"{k}일전"
            out_info.append({"name": s["name"], "reason": f"{tag} {a['pit']}구·{rest}일휴식"}); continue
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
    pfs = park_factors(games)                     # 구장별 득점환경
    wrc_by_p, team_base = load_lineup_wrc()        # 타순 반영용(로컬 캐시)

    out = []
    for g in todays:
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        pv = preview_data(g["gameId"])            # 선발·라인업을 한 번에
        sp = probable_starters(g["gameId"], pv)
        # 오늘 예고선발은 불펜 풀에서 명시적으로 제외(불펜 취급이던 스팟 선발도 커버)
        bpH, outH = available_bullpen(box, h, rotation, day, lg_ra9, ps=ps, exclude=[sp["home"][1]])
        bpA, outA = available_bullpen(box, a, rotation, day, lg_ra9, ps=ps, exclude=[sp["away"][1]])
        # 상대 이 경기 실점력(선발+가용불펜)
        pitchH, spH_ra9, spH_known, spH_inn = _game_ra9(sp["home"][1], ps, bpH, lg_ra9)
        pitchA, spA_ra9, spA_known, spA_inn = _game_ra9(sp["away"][1], ps, bpA, lg_ra9)
        oH, oA = rsg.get(h, lg), rsg.get(a, lg)
        # 지수(리그평균=1.0)로 만들고 수축(회귀). 극단 팀을 평균 쪽으로 당김.
        def idx(v, base):
            return 1 + (v / base - 1) * SHRINK
        oH_i, oA_i = idx(oH, lg), idx(oA, lg)
        pH_i, pA_i = idx(pitchH, lg_ra9), idx(pitchA, lg_ra9)   # 실점력(높을수록 잘 내줌)
        pf = pfs.get(g.get("stadium"), 1.0)                    # 구장 팩터(양팀 공통)
        # 기대득점: 리그평균 × 구장팩터 × 자기 공격지수 × 상대 실점력지수 × 홈보정 (log5식)
        erH = lg * pf * oH_i * pA_i * HOME_BOOST
        erA = lg * pf * oA_i * pH_i / HOME_BOOST
        # 승률: 단일경기 분산 반영 로지스틱(극단 압축)
        pH = 1 / (1 + math.exp(-(erH - erA) / WIN_SCALE))
        r2 = lambda v: round(v, 2)

        # ── 라인업(타순) 반영: 발표됐으면 팀 공격력을 오늘 9명 기준으로 보정 ──
        luH = lineup_from_preview(pv, "home")
        luA = lineup_from_preview(pv, "away")
        multH, detH = lineup_multiplier(luH, wrc_by_p, team_base.get(h))
        multA, detA = lineup_multiplier(luA, wrc_by_p, team_base.get(a))
        lineup_ready = bool(luH) and bool(luA)
        if lineup_ready:
            oHi2, oAi2 = idx(oH * multH, lg), idx(oA * multA, lg)
            erH2 = lg * pf * oHi2 * pA_i * HOME_BOOST
            erA2 = lg * pf * oAi2 * pH_i / HOME_BOOST
            pH2 = 1 / (1 + math.exp(-(erH2 - erA2) / WIN_SCALE))
        else:
            erH2, erA2, pH2 = erH, erA, pH

        out.append({
            "date": day, "home": h, "away": a,
            "homeName": config.TEAM_NAMES.get(h, h), "awayName": config.TEAM_NAMES.get(a, a),
            "spHome": sp["home"][0], "spAway": sp["away"][0],
            # 라인업 반영 전(팀 시즌 공격력 기준)
            "erHome": round(erH, 1), "erAway": round(erA, 1),
            "bandHome": _band(erH), "bandAway": _band(erA),
            "winHome": round(pH * 100), "winAway": round((1 - pH) * 100),
            # 라인업 반영 후(발표 전이면 반영 전과 동일)
            "lineupReady": lineup_ready,
            "erHomeLU": round(erH2, 1), "erAwayLU": round(erA2, 1),
            "bandHomeLU": _band(erH2), "bandAwayLU": _band(erA2),
            "winHomeLU": round(pH2 * 100), "winAwayLU": round((1 - pH2) * 100),
            "lineupHome": detH, "lineupAway": detA,
            "multHome": round(multH, 3), "multAway": round(multA, 3),
            "bpOutHome": outH, "bpOutAway": outA,
            "calc": {
                "lg": r2(lg), "boost": HOME_BOOST, "park": pf, "stadium": g.get("stadium"),
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
