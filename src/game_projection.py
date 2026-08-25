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
# 캘리브레이션(experiments/calibration.py): 5.5는 약간 과확신 → 6.5로 소폭 상향.
# 아래 날짜부터 적용(그 전 경기는 5.5 유지 — 과거 예측·성적 소급 변경 없음).
WIN_SCALE_NEW = 6.5
_WINSCALE_FROM = "2026-08-27"
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


def _bat_hand(s: str) -> str:
    """네이버 batsThrows/hitType → 타격손 'L'/'R'/'S'(스위치). 모르면 'R'."""
    s = s or ""
    if "양" in s or "좌우타" in s or "스위치" in s:
        return "S"
    if "좌타" in s or s.endswith("좌"):
        return "L"
    return "R"


def _throw_hand(s: str) -> str:
    """네이버 hitType/batsThrows → 투구손 'L'/'R'. 모르면 'R'."""
    s = s or ""
    if "좌투" in s or s.startswith("좌"):
        return "L"
    return "R"


def lineup_from_preview(pv: dict, side: str) -> list:
    """발표된 타격 라인업 → [(pcode, name, hand)] 타순 순서(선발투수 제외). 미발표면 [].
    hand=타격손 'L'/'R'/'S'."""
    key = "homeTeamLineUp" if side == "home" else "awayTeamLineUp"
    fl = (pv.get(key) or {}).get("fullLineUp") or []
    batters = [x for x in fl if x.get("positionName") != "선발투수"]
    if len(batters) < 9:
        return []
    return [(str(x.get("playerCode")), x.get("playerName"),
             _bat_hand(x.get("batsThrows") or x.get("hitType")))
            for x in batters[:9]]


def starter_hand(pv: dict, side: str) -> str:
    """예고선발 투구손 'L'/'R'. side=선발이 속한 팀."""
    key = "homeStarter" if side == "home" else "awayStarter"
    pi = (pv.get(key) or {}).get("playerInfo") or {}
    return _throw_hand(pi.get("hitType") or pi.get("batsThrows"))


# 리그평균 플래툰 스플릿(같은손↔반대손 wRC+ 격차, 점)과 정상 상대손 비율.
#   좌타가 우타보다 스플릿이 큼. 한 타자의 overall은 이미 '주로 우투 상대'라서,
#   오늘 상대 선발손이 정상 비율과 다른 만큼만 가감(이중계상 방지).
_PLATOON_GAP = {"L": 20.0, "R": 15.0}     # 반대손 − 같은손 (wRC+ 점)
_P_SAME = {"L": 0.28, "R": 0.72}          # 그 타자가 '같은손' 투수를 상대하는 평시 비율


def _platoon_adj(bat_hand: str, sp_hand: str) -> float:
    """상대 선발손 대비 타자 overall wRC+ 가감치(점). 스위치는 0(항상 반대손=overall)."""
    if bat_hand == "S" or not sp_hand:
        return 0.0
    gap, p_same = _PLATOON_GAP[bat_hand], _P_SAME[bat_hand]
    same = (bat_hand == sp_hand)
    return -(1 - p_same) * gap if same else +p_same * gap


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


def lineup_multiplier(batters: list, wrc_by_p: dict, team_base: float,
                      opp_sp_hand: str = None, opp_sp_inn: float = 9.0):
    """타순 순서 [(pcode,name,hand)] → (공격배수, [(name, wrc)…]). 미발표/데이터없으면 (1.0, []).
    배수 = 오늘 라인업 타순가중 wRC+ ÷ 팀 상위9 베이스 wRC+ (0.85~1.15 제한).
    상대 선발손(opp_sp_hand)이 주어지면 리그평균 플래툰을 가감하되, 그 선발이 던지는
    이닝 비율(opp_sp_inn/9)만큼만 적용한다(불펜 구간은 좌우 섞여 상쇄)."""
    if not batters or not team_base:
        return 1.0, []
    share = max(0.0, min(1.0, opp_sp_inn / 9.0)) if opp_sp_hand else 0.0
    vals, detail, wsum = 0.0, [], 0.0
    for i, b in enumerate(batters):
        pc, name = b[0], b[1]
        hand = b[2] if len(b) > 2 else "R"
        w = _BATORDER_PA[i] if i < len(_BATORDER_PA) else 3.8
        wrc = wrc_by_p.get(pc)
        if wrc is None:            # 무기록(신인·콜업) → 팀 베이스로 대체
            wrc = team_base
        wrc_eff = wrc + _platoon_adj(hand, opp_sp_hand) * share
        vals += w * wrc_eff; wsum += w
        detail.append((name, round(wrc_eff)))
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


def _schedule_energy(is_dh_second: bool):
    """일정 에너지 배수(1.0=보통). 실증(experiments/schedule_fatigue.py) 결과
    휴식일·연속 원정·이동 거리는 유의한 효과가 없어 제거하고,
    유일하게 실재하는 '더블헤더 2차전'(같은 날 2경기째)만 반영한다.
      · 실측 −0.85점/경기(≈ −15%). 자기 공격에 적용."""
    if is_dh_second:
        return 0.85, "더블헤더 2차전"
    return 1.0, ""


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


def available_bullpen(box, team, rotation, asof, lg_ra9, ps=None, exclude=None, fatigue=True):
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
        return lg_ra9, out_info, 1.0
    tot = sum(s["outs"] for s in avail)

    # 누적 피로: 결장까진 아니어도 최근 2일 투구가 쌓인 불펜은 오늘 실점력이 소폭 상승.
    #   실증(experiments/bullpen_fatigue.py): 완전휴식 RA9 4.69 vs 최근 등판 5.00
    #   → 약 +6~8%(+0.3~0.4 RA9). 그 관측에 맞춰 완만하게(상한 +10%) 반영.
    def _fatigue(s):
        if not fatigue:
            return 1.0
        recent = sum(a["pit"] for a in s["apps"]
                     if 1 <= (d0 - datetime.date.fromisoformat(a["date"])).days <= 2)
        return 1.0 + min(0.10, recent / 300.0)      # 최근2일 30구≈+10%(상한)

    ra9 = sum((s["ra9"] or lg_ra9) * _fatigue(s) * s["outs"] for s in avail) / tot
    fat_idx = sum(_fatigue(s) * s["outs"] for s in avail) / tot   # 1.0=팔팔, >1=지침
    return ra9, out_info, round(fat_idx, 3)


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


_FINISHED = {"RESULT", "ENDED"}   # 종료 경기(스코어 확정) 상태
# 업셋 다이내미즘(불펜 누적 피로·일정 에너지·업셋 지수) 적용 시작일.
# 그 전 경기는 기존 모델과 동일하게 계산 — 오늘 이전 예측·성적을 소급 변경하지 않음.
_DYN_FROM = "2026-08-26"


def _project_day(day, games, box, rsg, lg, lg_ra9, ps, rotation, pfs, wrc_by_p, team_base):
    """하루치(day) 경기들을 기대 스코어 dict 리스트로. 공유 컨텍스트는 인자로 받는다."""
    todays = [g for g in games if g.get("gameDate") == day and not g.get("cancel")]
    # 더블헤더: 같은 팀이 오늘 2경기 이상이면 나중 경기(들)가 2차전(피로) — (gameId, 팀) 집합
    _byteam = defaultdict(list)
    for gg in todays:
        _byteam[gg.get("homeTeamCode")].append(gg)
        _byteam[gg.get("awayTeamCode")].append(gg)
    dh_second = set()
    for tc, gl in _byteam.items():
        gl.sort(key=lambda x: x.get("gameDateTime", ""))
        for gg in gl[1:]:
            dh_second.add((gg.get("gameId"), tc))
    ws = WIN_SCALE_NEW if day >= _WINSCALE_FROM else WIN_SCALE   # 승률 스케일(날짜 컷오프)
    out = []
    for g in todays:
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        pv = preview_data(g["gameId"])            # 선발·라인업을 한 번에
        sp = probable_starters(g["gameId"], pv)
        # 오늘 예고선발은 불펜 풀에서 명시적으로 제외(불펜 취급이던 스팟 선발도 커버)
        dyn = day >= _DYN_FROM                     # 업셋 다이내미즘 적용 여부(날짜 컷오프)
        bpH, outH, fatH = available_bullpen(box, h, rotation, day, lg_ra9, ps=ps, exclude=[sp["home"][1]], fatigue=dyn)
        bpA, outA, fatA = available_bullpen(box, a, rotation, day, lg_ra9, ps=ps, exclude=[sp["away"][1]], fatigue=dyn)
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
        # 일정 에너지(휴식·원정 연전) — 지친 팀은 자기 공격이 소폭 하락(업셋 다이내미즘)
        enH, enWhyH = _schedule_energy((g.get("gameId"), h) in dh_second) if dyn else (1.0, "")
        enA, enWhyA = _schedule_energy((g.get("gameId"), a) in dh_second) if dyn else (1.0, "")
        # 기대득점: 리그평균 × 구장팩터 × 자기 공격지수 × 상대 실점력지수 × 홈보정 × 에너지
        erH = lg * pf * oH_i * pA_i * HOME_BOOST * enH
        erA = lg * pf * oA_i * pH_i / HOME_BOOST * enA
        # 승률: 단일경기 분산 반영 로지스틱(극단 압축)
        pH = 1 / (1 + math.exp(-(erH - erA) / ws))
        r2 = lambda v: round(v, 2)

        # ── 라인업(타순) 반영: 발표됐으면 팀 공격력을 오늘 9명 기준으로 보정 ──
        #   + 리그평균 플래툰: 각 팀 타선을 '상대 선발손' 대비 가감(선발 이닝만큼).
        spHand_home = starter_hand(pv, "home")   # 홈 선발 투구손
        spHand_away = starter_hand(pv, "away")   # 원정 선발 투구손
        luH = lineup_from_preview(pv, "home")
        luA = lineup_from_preview(pv, "away")
        # 홈 타선은 '원정 선발'을 상대 / 원정 타선은 '홈 선발'을 상대
        multH, detH = lineup_multiplier(luH, wrc_by_p, team_base.get(h), spHand_away, spA_inn)
        multA, detA = lineup_multiplier(luA, wrc_by_p, team_base.get(a), spHand_home, spH_inn)
        lineup_ready = bool(luH) and bool(luA)
        # 라인업 반영 공격지수(미발표면 multH=multA=1.0이라 반영 전과 동일)
        oHi2, oAi2 = idx(oH * multH, lg), idx(oA * multA, lg)
        if lineup_ready:
            erH2 = lg * pf * oHi2 * pA_i * HOME_BOOST * enH
            erA2 = lg * pf * oAi2 * pH_i / HOME_BOOST * enA
            pH2 = 1 / (1 + math.exp(-(erH2 - erA2) / ws))
        else:
            erH2, erA2, pH2 = erH, erA, pH

        # ── 업셋 지수: 매치업 요소(선발·불펜피로·구장·일정)가 '공격 약팀'에게
        #    순위 기반 나이브 예측 대비 얼마나 승산을 더 주나 ──
        naiveH = 1 / (1 + math.exp(-((lg * oH_i * HOME_BOOST) - (lg * oA_i / HOME_BOOST)) / ws))
        ud_home = oH_i < oA_i                       # 홈이 공격 약팀?
        ud_win = round((pH2 if ud_home else 1 - pH2) * 100)
        ud_naive = round((naiveH if ud_home else 1 - naiveH) * 100)
        upset_lift = ud_win - ud_naive              # 매치업이 약팀에게 더 준 승산(%p)
        underdog = h if ud_home else a
        is_upset = dyn and ud_win >= 45 and upset_lift >= 3
        # 업셋 사유(약팀 관점): 어떤 요소가 도왔나
        ur = []
        favp = pA_i if ud_home else pH_i            # 약팀이 상대할 실점력(상대 투수진)
        udp = pA_i if not ud_home else pH_i
        if favp > udp + 0.03:
            ur.append("상대 투수진 열세")            # 약팀 상대 투수진이 더 잘 내줌
        fav_fat = fatA if ud_home else fatH          # 상대(강팀) 불펜 피로
        if fav_fat >= 1.04:
            ur.append("상대 불펜 피로")
        fav_energy = enA if ud_home else enH
        if fav_energy < 1.0:                          # 상대가 더블헤더 2차전 등
            ur.append("상대 더블헤더 피로")

        # 실제 결과(종료 경기): 네이버 최종 스코어. 진행 전이면 None.
        # RESULT=확정, ENDED=종료 직후(스코어는 이미 확정) 둘 다 종료로 취급.
        status = g.get("statusCode")
        finished = status in _FINISHED
        actH = g.get("homeTeamScore") if finished else None
        actA = g.get("awayTeamScore") if finished else None
        out.append({
            "date": day, "home": h, "away": a, "gameId": g.get("gameId"),
            "status": status, "actualHome": actH, "actualAway": actA,
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
            "spHandHome": spHand_home, "spHandAway": spHand_away,
            "bpOutHome": outH, "bpOutAway": outA,
            # 업셋(공격 약팀이 매치업 덕에 순위 예상보다 승산 큼)
            "upset": is_upset, "underdog": underdog,
            "underdogName": config.TEAM_NAMES.get(underdog, underdog),
            "underdogWin": ud_win, "upsetLift": upset_lift,
            "upsetWhy": " · ".join(ur),
            "calc": {
                "lg": r2(lg), "boost": HOME_BOOST, "park": pf, "stadium": g.get("stadium"),
                "offHome": r2(oH), "offAway": r2(oA), "oIdxHome": r2(oH_i), "oIdxAway": r2(oA_i),
                "oIdxHomeLU": r2(oHi2), "oIdxAwayLU": r2(oAi2),  # 라인업 반영 공격지수
                "spHomeRa9": r2(spH_ra9), "spHomeKnown": spH_known, "spHomeInn": spH_inn,
                "bpHome": r2(bpH), "bpHomeInn": r2(9 - spH_inn), "fatHome": fatH,
                "pitchHome": r2(pitchH), "pIdxHome": r2(pH_i),
                "spAwayRa9": r2(spA_ra9), "spAwayKnown": spA_known, "spAwayInn": spA_inn,
                "bpAway": r2(bpA), "bpAwayInn": r2(9 - spA_inn), "fatAway": fatA,
                "pitchAway": r2(pitchA), "pIdxAway": r2(pA_i),
                "enHome": enH, "enAway": enA, "enWhyHome": enWhyH, "enWhyAway": enWhyA,
            },
        })
    return out


def project_games(games: list, box, ref_date: str = None) -> dict:
    """오늘의 경기 기대 스코어. 반환 {date, games, sections}.
    · 오늘 경기가 있으면 오늘 한 섹션(경기 시작·종료돼도 그날 내내 유지).
    · 오늘 경기가 없으면(휴식일=월요일 등) '지난 경기 결과' + '다음 경기 예고' 두 섹션.
    sections=[{date, kind, label, games:[...]}] 순서대로 렌더. date/games는 첫 섹션(하위호환)."""
    today = ref_date or datetime.date.today().isoformat()
    PREGAME = {"BEFORE", "READY"}

    # 공유 컨텍스트(한 번만 계산)
    rsg, lg = team_offense(games)
    lg_ra9 = lg
    ps = _pitcher_stats(box)
    rotation = identify_rotation(box)
    pfs = park_factors(games)
    wrc_by_p, team_base = load_lineup_wrc()

    def day_of(d):
        return _project_day(d, games, box, rsg, lg, lg_ra9, ps, rotation, pfs, wrc_by_p, team_base)

    def wd_label(d):
        w = "월화수목금토일"[datetime.date.fromisoformat(d).weekday()]
        return f"{d}({w})"

    sections = []
    if any(g["gameDate"] == today and not g.get("cancel") for g in games):
        sections.append({"date": today, "kind": "today",
                         "label": f"오늘의 경기 · {wd_label(today)}", "games": day_of(today)})
    else:
        # 휴식일: 마지막 경기일(결과) + 다음 경기일(예고)
        past = [g["gameDate"] for g in games
                if g.get("gameDate", "") < today and not g.get("cancel")
                and g.get("statusCode") in _FINISHED]
        last = max(past, default=None)
        fut = [g["gameDate"] for g in games if g.get("statusCode") in PREGAME
               and not g.get("cancel") and g.get("gameDate", "") > today]
        nextd = min(fut, default=None)
        if last:
            sections.append({"date": last, "kind": "result",
                             "label": f"지난 경기 결과 · {wd_label(last)}", "games": day_of(last)})
        if nextd:
            sections.append({"date": nextd, "kind": "preview",
                             "label": f"다음 경기 예고 · {wd_label(nextd)}", "games": day_of(nextd)})

    # 이미 시작·종료된 경기는 '경기 전 마지막 예측'으로 고정 표시(자기 결과 오염 방지).
    #   매 빌드 재계산하면 그 경기 결과가 시즌 입력(RS/G·불펜·선발·파크)에 되먹임돼
    #   예측이 미세하게 흔들리므로, 저장된 사전 예측값으로 덮어써 카드·성적표를 일치시킨다.
    _apply_frozen(sections)
    primary = sections[0] if sections else {"date": None, "games": []}
    return {"date": primary["date"], "games": primary["games"], "sections": sections}


_PREDLOG_PATH = f"{config.DATA_DIR}/predictions.json"


def _apply_frozen(sections):
    """시작·종료된 경기의 표시 예측을 predictions.json의 '경기 전 마지막 값'으로 덮어쓴다.
    경기 후 자기 결과가 시즌 입력에 되먹임돼 재계산값이 흔들리는 것을 막고,
    오늘의 경기 카드와 예측 성적표가 동일한 사전 예측을 보이게 한다."""
    import json
    try:
        log = json.loads(open(_PREDLOG_PATH, encoding="utf-8").read())
    except Exception:
        return
    PRE = {"BEFORE", "READY"}
    for sec in sections:
        for g in sec.get("games", []):
            if g.get("status") in PRE:
                continue                       # 경기 전이면 라이브 재계산 그대로
            e = log.get(g.get("gameId"))
            if not e or e.get("predHome") is None:
                continue                       # 저장된 사전 예측 없으면 재계산 유지
            g["erAway"] = e.get("predAwayPre", g["erAway"])
            g["erHome"] = e.get("predHomePre", g["erHome"])
            g["erAwayLU"] = e.get("predAway", g.get("erAwayLU"))
            g["erHomeLU"] = e.get("predHome", g.get("erHomeLU"))
            g["bandAway"], g["bandHome"] = _band(g["erAway"]), _band(g["erHome"])
            g["bandAwayLU"], g["bandHomeLU"] = _band(g["erAwayLU"]), _band(g["erHomeLU"])
            g["winAwayLU"] = e.get("winAway", g.get("winAwayLU"))
            g["winHomeLU"] = e.get("winHome", g.get("winHomeLU"))
            g["winAway"], g["winHome"] = g["winAwayLU"], g["winHomeLU"]
            if "lineupReady" in e:
                g["lineupReady"] = e["lineupReady"]


def save_prediction_log(projections: dict, games: list, path: str = None) -> str:
    """경기별 예측(반영 전/후)+실제 결과를 data/predictions.json에 누적 저장.
    · 예측은 '경기 전(BEFORE/READY)'일 때만 갱신 → 첫 구 시점의 마지막 값으로 고정.
      경기가 시작되면 갱신하지 않아, 그 경기 결과가 시즌 입력에 되먹임돼 예측이
      흔들리는 자기오염을 막는다. (표시는 _apply_frozen이 같은 고정값을 보여줌)
    · 실제 결과는 종료(RESULT/ENDED) 시 채우고, 날짜가 지난 뒤에도 games 전체를 훑어
      로그에 있는 미완료 경기의 결과를 뒤늦게 backfill한다.
    반환: 저장 경로."""
    import json
    import datetime
    path = path or _PREDLOG_PATH
    try:
        log = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        log = {}
    today = datetime.date.today().isoformat()

    def _grade(e):
        ah, aa = e.get("actualHome"), e.get("actualAway")
        if ah is None or aa is None or ah == aa:
            e["correct"] = None                 # 무승부·미종료는 판정 보류
            return
        # 예측 승자(반영 후 기대득점) vs 실제 승자
        pred_home_win = e.get("predHome", 0) >= e.get("predAway", 0)
        e["correct"] = bool(pred_home_win == (ah > aa))

    # 1) 표시 슬레이트(오늘/결과/예고 전부): 예측 업서트 + 상태·결과 반영.
    #    당일·예정 경기만 예측을 갱신하므로 카드(=최신 재계산)와 로그가 항상 같은 값.
    secs = projections.get("sections")
    slate = [g for s in secs for g in s["games"]] if secs else projections.get("games", [])
    for g in slate:
        gid = g.get("gameId")
        if not gid:
            continue
        e = log.get(gid, {})
        pregame = g.get("status") in ("BEFORE", "READY")
        have = "predHome" in e
        # 경기 전(pregame)일 때만 예측 갱신 → 첫 구 시점의 마지막 값으로 자연 고정.
        #   시작 후엔 갱신 안 함(자기 결과 오염 방지). 미기록 경기는 1회 폴백 캡처.
        if g.get("date", "") >= today and (pregame or not have):
            e.pop("frozen", None)               # 구버전 잔재 정리
            e.update({
                "date": g["date"], "away": g["away"], "home": g["home"],
                "awayName": g["awayName"], "homeName": g["homeName"],
                "spAway": g["spAway"], "spHome": g["spHome"],
                "predAwayPre": g["erAway"], "predHomePre": g["erHome"],
                "predAway": g.get("erAwayLU", g["erAway"]),
                "predHome": g.get("erHomeLU", g["erHome"]),
                "winAway": g.get("winAwayLU", g["winAway"]),
                "winHome": g.get("winHomeLU", g["winHome"]),
                "lineupReady": g.get("lineupReady", False),
            })
        e["status"] = g.get("status")
        if g.get("actualHome") is not None:
            e["actualHome"], e["actualAway"] = g["actualHome"], g["actualAway"]
            _grade(e)
        log[gid] = e

    # 2) 과거 경기 backfill: 로그에 있는데 결과가 빈 경기를 games에서 채움
    by_id = {x.get("gameId"): x for x in games}
    for gid, e in log.items():
        if e.get("actualHome") is None:
            gg = by_id.get(gid)
            if gg and gg.get("statusCode") in _FINISHED:
                e["actualHome"], e["actualAway"] = gg.get("homeTeamScore"), gg.get("awayTeamScore")
                e["status"] = gg.get("statusCode"); e["frozen"] = True
                _grade(e)

    open(path, "w", encoding="utf-8").write(json.dumps(log, ensure_ascii=False, indent=1))
    return path
