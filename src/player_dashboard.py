# -*- coding: utf-8 -*-
"""
player_dashboard.py — 선수 평가 대시보드 (HTML) 생성기
========================================================

player_eval.py의 투수/타자 평가 결과를 data/players.html 로 만듭니다.

[구성]
  · 투수 사분면 / 타자 BABIP운 / 파워유형 산점도 (그래프 클릭 = 랜덤 선수)
  · 타자 랜덤픽 → 5툴 레이더(선구/컨택/타격/파워/주루) + 플레이트 디서플린
  · 투수 랜덤픽 → 구종 아스널(구종별 구위·구사율·헛스윙)
  · FCB 승리기여 리더보드 (kbostuff 고유 지표)
  · 스크리닝 테이블 6종 (더 보기 확장) + 지표별 공식 툴팁
"""

import json
from datetime import date
from pathlib import Path

import pandas as pd

import config
from dashboard import TEAM_COLORS, logo_map, _gen_stamp  # 팀 색/로고/갱신시각 재사용

COLLAPSE_AT = 8  # 스크리닝 테이블 접기 기준


def _round(v, n=3):
    return None if pd.isna(v) else round(float(v), n)


def _arsenal(details) -> list:
    """
    pitch_type_details(JSON dict) → 구사율 순 구종 리스트.
    구사율 1% 미만(사실상 실투/오분류)은 잡음이라 버립니다.
    """
    if not isinstance(details, dict):
        return []
    items = []
    for code, d in details.items():
        usage = d.get("usage_pct", 0) or 0
        if usage < 1.0:
            continue
        items.append({
            "code": code, "group": d.get("group", ""),
            "usage": round(usage, 1),
            "stuff": round(d.get("k_stuff_v2", 0) or 0, 1),
            "loc": round(d.get("k_location_v3", 0) or 0, 1),
            "heart": round((d.get("heart_pct", 0) or 0) * 100, 1),
            "whiff": round((d.get("whiff", 0) or 0) * 100, 1),
            "speed": d.get("speed"),
        })
    items.sort(key=lambda x: -x["usage"])
    return items


# ── 지표별 산정 공식 (헤더 호버 툴팁) ──
FORMULAS = {
    "투수": "선수명. 네이버 경기별 박스스코어를 시즌 합산해 성적을 계산합니다.",
    "이닝": "던진 총 이닝 (아웃카운트÷3).",
    "ERA": "평균자책점 = 9 × 자책점 ÷ 이닝. 낮을수록 좋음(결과 지표).",
    "FIP": "수비무관 평균자책 = (13×피홈런 + 3×(볼넷+사구) − 2×삼진) ÷ 이닝 + 상수. "
           "수비·운을 걷어낸 투수 본연의 성적.",
    "구위+": "K-Stuff+. 구속·무브먼트 등 공의 물리적 특성만으로 평가한 구위. 100=리그평균.",
    "제구+": "K-Control+. 투구 로케이션(제구) 품질 지수. 100=리그평균.",
    "타자": "선수명. kbostuff.app 트래킹 기반 타자 지표.",
    "타석": "타석 수(PA). 100타석 이상만 평가 대상.",
    "종합+": "Batter Metrics+ overall. 선구·컨택·타격·파워·주루 종합 100 기준.",
    "wRC+순수": "파크팩터·타구 비거리 보정 득점창출력. 100=리그평균, 130=평균보다 30%↑.",
    "운": "BABIP − 리그평균 BABIP. 음수=불운(반등), 양수=거품. "
          "발 빠른 타자는 실력으로 높은 BABIP를 유지하기도 합니다.",
    "wRC+실제": "실제 결과 기반 wRC+ (park_factor로 구장 보정 완료). 100=리그평균, 130=평균보다 30%↑.",
    "파크팩터": "홈구장의 득점 환경 지수. 1.00=리그평균, <1=투수친화(득점 억제), >1=타자친화. 직전+올 시즌 평균으로 안정화(반시즌 노이즈·신구장 반영).",
    "구장억제": "리그 평균 대비 홈구장이 득점을 억제하는 비율 = (1 − 파크팩터)×100%. 클수록 홈런·장타 raw 기록이 눌림.",
    "구장차": "(구버전) 순수 wRC+ − 이벤트 wRC+. 실제 파크팩터와 상관 ≈ 0이라 구장 지표로는 폐기, 파크팩터로 대체.",
    "승리기여": "FCB. 협조적 게임이론(Shapley value)으로 '득점이 난 이닝'에서 각 타자의 "
                "승리 기여 몫을 공정 분배해 누적. ⚠️ 클러치는 잘 지속되지 않아 "
                "미래 예측이 아닌 '지금까지의 서사'를 설명하는 지표입니다.",
    "경기당SRC": "경기당 상황 기여(Situational Run Contribution) 평균.",
}


def _tip(label, key):
    return f'<span class="tip" data-tip="{FORMULAS.get(key, "").replace(chr(34), "&quot;")}">{label}</span>'


def _pitcher_rows(df):
    out = []
    for i, (_, r) in enumerate(df.iterrows()):
        hide = ' class="row-hidden"' if i >= COLLAPSE_AT else ""
        out.append(
            f"<tr{hide}><td>{r['name']}</td><td>{r['team_name']}</td>"
            f"<td>{r['ip']:.1f}</td><td>{r['era']:.2f}</td><td>{r['fip']:.2f}</td>"
            f"<td>{r['k_stuff_v2']:.1f}</td><td>{r['k_control_v2']:.1f}</td></tr>")
    return "".join(out) or '<tr><td colspan="7" class="empty">해당 없음</td></tr>'


def _batter_rows(df, extra_col):
    out = []
    for i, (_, r) in enumerate(df.iterrows()):
        hide = ' class="row-hidden"' if i >= COLLAPSE_AT else ""
        extra = f"{r[extra_col]:+.3f}" if extra_col == "luck" else f"{r[extra_col]:+.1f}"
        out.append(
            f"<tr{hide}><td>{r['player_name']}</td><td>{r['team_name']}</td>"
            f"<td>{int(r['n_pa'])}</td><td>{r['overall_plus']:.1f}</td>"
            f"<td>{_round(r['wrc_plus_pure'], 1) or '-'}</td><td>{extra}</td></tr>")
    return "".join(out) or '<tr><td colspan="6" class="empty">해당 없음</td></tr>'


def _park_rows(df):
    """구장 피해자 카드: 선수·팀·타석·wRC+(구장보정)·홈 파크팩터·억제%."""
    out = []
    for i, (_, r) in enumerate(df.iterrows()):
        hide = ' class="row-hidden"' if i >= COLLAPSE_AT else ""
        pf = float(r["park_factor"])
        supp = (1.0 - pf) * 100  # 리그 평균 대비 득점 억제율
        out.append(
            f"<tr{hide}><td>{r['player_name']}</td><td>{r['team_name']}</td>"
            f"<td>{int(r['n_pa'])}</td><td>{r['wrc_plus_event']:.1f}</td>"
            f"<td>{pf:.3f}</td><td>−{supp:.1f}%</td></tr>")
    return "".join(out) or '<tr><td colspan="6" class="empty">해당 없음</td></tr>'


def _fcb_rows(df):
    out = []
    for i, (_, r) in enumerate(df.iterrows()):
        hide = ' class="row-hidden"' if i >= COLLAPSE_AT else ""
        wc = _round(r["wins_contributed"], 1)
        src = _round(r["avg_src_per_game"], 2)
        out.append(
            f"<tr{hide}><td>{r['player_name']}</td><td>{r['team_name']}</td>"
            f"<td>{int(r['n_pa'])}</td><td>{wc if wc is not None else '-'}</td>"
            f"<td>{src if src is not None else '-'}</td>"
            f"<td>{r['overall_plus']:.1f}</td></tr>")
    return "".join(out) or '<tr><td colspan="6" class="empty">해당 없음</td></tr>'


def _more(n, tb):
    if n <= COLLAPSE_AT:
        return ""
    return f'<button class="more" data-tb="{tb}">＋ 더 보기 ({n - COLLAPSE_AT}명 더)</button>'


def _load_statiz(season):
    """
    build_statiz.py가 만든 data/statiz_*.csv (Statiz WAR 스냅샷)를 읽어
    대시보드 레코드로 만든다. 파일이 없으면(CI에 Statiz 없음) 빈 리스트 반환.

    반환: (batters, relievers) — 각각 색·팀명이 붙은 dict 리스트
    """
    import csv as _csv

    def _rows(name):
        p = Path(config.DATA_DIR) / f"{name}_{season}.csv"
        if not p.exists():
            return []
        return list(_csv.DictReader(open(p, encoding="utf-8-sig")))

    def _dec(rows, fields):
        out = []
        for r in rows:
            code = r["team"]
            rec = {"name": r["name"], "team": code,
                   "teamName": config.TEAM_NAMES.get(code, code),
                   "color": TEAM_COLORS.get(code, "#888")}
            for f in fields:
                try:
                    rec[f] = float(r[f])
                except (ValueError, KeyError):
                    rec[f] = r.get(f)
            out.append(rec)
        return out

    bats = _dec(_rows("statiz_batters"),
                ["pos", "owar", "dwar", "war", "pa", "wrcplus",
                 "bbpct", "kpct", "iso", "sb", "cs", "gdp"])
    for b in bats:
        b["pa"] = int(b["pa"]) if b["pa"] is not None else 0
    rel = _dec(_rows("statiz_relievers"),
               ["war", "era", "fip", "ip", "g", "gf", "sv", "hd",
                "k9", "bb9", "kbb"])
    for r in rel:
        for k in ("g", "gf", "sv", "hd"):
            r[k] = int(r[k]) if r.get(k) is not None else 0
    return bats, rel


def save_player_dashboard(pitchers, batters, p_screens, b_screens, lg_era,
                          latest_game=None, hotcold=None, bullpen_form=None,
                          starter_form=None, streaks=None, monthly=None, splits=None):
    """선수 평가 대시보드를 data/players.html 로 저장합니다.

    latest_game  : 반영된 최신 경기일(투수 박스스코어 기준). 자막에 표시.
    hotcold      : recent_form() 결과(불방망이/물방망이). 없으면 카드 숨김.
    bullpen_form : recent_pitch_form(role='relief') 결과(수호신/방화범).
    starter_form : recent_pitch_form(role='start') 결과(에이스/붕괴).
    streaks      : hit_streaks() 결과(연속 안타·출루 행진).
    """
    def hc_rec(r):
        return {**r,
                "teamName": config.TEAM_NAMES.get(r["team"], r["team"]),
                "color": TEAM_COLORS.get(r["team"], "#888")}

    def pit_rec(r):
        return {
            "pcode": str(r["pcode"]),
            "name": r["name"], "team": r["team"],
            "teamName": config.TEAM_NAMES.get(r["team"], r["team"]),
            "color": TEAM_COLORS.get(r["team"], "#888"),
            "stuff": _round(r["k_stuff_v2"], 1), "control": _round(r["k_control_v2"], 1),
            "era": _round(r["era"], 2), "fip": _round(r["fip"], 2),
            "gap": _round(r["era_fip_gap"], 2), "ip": _round(r["ip"], 1),
            "type": r["type"],
            # 로케이션(Gap 축) — K-Location+ 와 구위·로케이션 격차
            "loc": _round(r.get("k_location"), 1),
            "locGap": _round(r.get("stuff_loc_gap"), 1),
            "locType": r.get("loc_type", ""),
            "heart": _round((r.get("heart_pct") or 0) * 100, 1),
            "edge": _round((r.get("edge_pct") or 0) * 100, 1),
            "waste": _round((r.get("waste_pct") or 0) * 100, 1),
            "whiff": _round((r.get("whiff_rate") or 0) * 100, 1),
            "csw": _round((r.get("csw_rate") or 0) * 100, 1),
            "speed": _round(r.get("avg_speed"), 1),
            "arsenal": _arsenal(r.get("pitch_type_details")),
        }

    def bat_rec(r):
        return {
            "pcode": str(r["pcode"]),
            "name": r["player_name"], "team": r["team_code"],
            "teamName": config.TEAM_NAMES.get(r["team_code"], r["team_code"]),
            "color": TEAM_COLORS.get(r["team_code"], "#888"),
            "woba": _round(r["woba_inplay"]), "babip": _round(r["babip"]),
            "power": _round(r["power_plus"], 1), "hr": _round(r["hr_plus"], 1),
            "pa": int(r["n_pa"]), "overall": _round(r["overall_plus"], 1),
            "wrcPure": _round(r["wrc_plus_pure"], 1),
            "luck": _round(r["luck"]), "luckType": r["luck_type"],
            "powerType": r["power_type"],
            # 5툴 레이더
            "eye": _round(r["eye_plus"], 1), "vision": _round(r["vision_plus"], 1),
            "hit": _round(r["hit_plus"], 1), "baseR": _round(r["baserunning_plus"], 1),
            # 플레이트 디서플린
            "chase": _round((r.get("chase_rate") or 0) * 100, 1),
            "contact": _round((r.get("contact_rate") or 0) * 100, 1),
            "iso": _round(r.get("iso_inplay")),
            # FCB
            "wins": _round(r.get("wins_contributed"), 1),
            "avgSrc": _round(r.get("avg_src_per_game"), 2),
        }

    statiz_bats, relievers = _load_statiz(config.SEASON)
    data = {
        "generated": str(date.today()), "season": config.SEASON,
        "lgEra": round(lg_era, 2), "stuffHigh": 105, "stuffLow": 97,
        "lgBabip": _round(batters.attrs.get("lg_babip"), 3),
        "logos": logo_map(),
        "pitchers": [pit_rec(r) for _, r in pitchers.iterrows()],
        "batters": [bat_rec(r) for _, r in batters.iterrows()],
        # Statiz WAR 스냅샷 (있을 때만; 없으면 빈 리스트 → 카드 자동 숨김)
        "statizBatters": statiz_bats,
        "relievers": relievers,
        # 최근 폼(원자료 풀; 지표·Δ·랭킹은 JS 토글) — 없으면 빈 → 카드 자동 숨김
        "hotcold": ({"window": hotcold["window"], "minPa": hotcold["minPa"],
                     "players": [hc_rec(r) for r in hotcold["players"]]}
                    if hotcold else {"window": 0, "minPa": 0, "players": []}),
        "bullpenForm": ({"window": bullpen_form["window"], "minApp": bullpen_form["minApp"],
                         "players": [hc_rec(r) for r in bullpen_form["players"]]}
                        if bullpen_form else {"window": 0, "minApp": 0, "players": []}),
        "starterForm": ({"window": starter_form["window"], "minApp": starter_form["minApp"],
                         "players": [hc_rec(r) for r in starter_form["players"]]}
                        if starter_form else {"window": 0, "minApp": 0, "players": []}),
        "streaks": ({"minStreak": streaks["minStreak"],
                     "hit": [hc_rec(r) for r in streaks["hit"]],
                     "onbase": [hc_rec(r) for r in streaks["onbase"]]}
                    if streaks else {"minStreak": 0, "hit": [], "onbase": []}),
        "monthly": ({"months": monthly["months"],
                     "batters": {m: [hc_rec(r) for r in v] for m, v in monthly["batters"].items()},
                     "pitchers": {m: [hc_rec(r) for r in v] for m, v in monthly["pitchers"].items()}}
                    if monthly else {"months": [], "batters": {}, "pitchers": {}}),
        "splits": ({"minPa": splits["minPa"],
                    "homeStrong": [hc_rec(r) for r in splits["homeStrong"]],
                    "awayStrong": [hc_rec(r) for r in splits["awayStrong"]]}
                   if splits else {"minPa": 0, "homeStrong": [], "awayStrong": []}),
    }

    html = _TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__SEASON__", str(config.SEASON))
    html = html.replace("__STAMP__", _gen_stamp())
    html = html.replace("__LATEST__", latest_game or "-")
    html = html.replace("__LG_ERA__", f"{lg_era:.2f}")

    for key, scr, tb in [
        ("UNLUCKY", p_screens["unlucky"], "tb_unlucky"),
        ("TIMEBOMB", p_screens["timebomb"], "tb_timebomb"),
        ("VICTIM", p_screens["defense_victim"], "tb_victim"),
    ]:
        html = html.replace(f"__T_{key}__", _pitcher_rows(scr))
        html = html.replace(f"__M_{key}__", _more(len(scr), tb))

    for key, scr, col, tb in [
        ("UNDERVALUED", b_screens["undervalued"], "luck", "tb_under"),
        ("BUBBLE", b_screens["bubble"], "luck", "tb_bubble"),
    ]:
        html = html.replace(f"__T_{key}__", _batter_rows(scr, col))
        html = html.replace(f"__M_{key}__", _more(len(scr), tb))

    # 구장 카드는 컬럼 구성이 달라 전용 렌더러 사용
    park = b_screens["park_victim"]
    html = html.replace("__T_PARK__", _park_rows(park))
    html = html.replace("__M_PARK__", _more(len(park), "tb_park"))

    clutch = b_screens["clutch"]
    html = html.replace("__T_CLUTCH__", _fcb_rows(clutch))
    html = html.replace("__M_CLUTCH__", _more(len(clutch), "tb_clutch"))

    out = Path(config.DATA_DIR) / "players.html"
    out.write_text(html, encoding="utf-8")
    return out


# ──────────────────────────────────────────────────────────────
# HTML 템플릿
# ──────────────────────────────────────────────────────────────

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KBO __SEASON__ 선수 평가 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #10141c; --card: #1a212e; --line: #2a3345;
    --text: #e8ecf3; --muted: #8a94a8;
    --green: #3ecf8e; --red: #ff6b6b; --amber: #ffb454; --blue: #4a90d9;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; line-height: 1.7; }
  .sub .stamp { color: var(--text); }
  .sub .stamp b { color: var(--green); }

  /* 대시보드 전환 네비게이션 */
  .nav { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }
  .nav a { text-decoration: none; padding: 7px 14px; border-radius: 999px; font-size: 13px;
    font-weight: 600; border: 1px solid var(--line); color: var(--muted); background: var(--card); }
  .nav a:hover { color: var(--text); border-color: #3a4560; }
  .nav a.active { background: var(--green); color: #0b0e14; border-color: var(--green); }
  .nav a.home { font-weight: 400; padding: 7px 12px; }
  .refresh-btn { margin-left: auto; padding: 7px 14px; border-radius: 999px; font-size: 13px;
    font-weight: 700; border: 1px solid #3a4560; color: var(--text); background: var(--card);
    cursor: pointer; font-family: inherit; }
  .refresh-btn:hover:not(:disabled) { border-color: var(--green); color: var(--green); }
  .refresh-btn:disabled { opacity: 0.55; cursor: progress; }
  .refresh-msg { font-size: 12px; color: var(--muted); align-self: center; }
  /* 팀 토글 + 검색 필터바 */
  .filterbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    margin-bottom: 14px; padding: 10px 12px; background: var(--card);
    border: 1px solid var(--line); border-radius: 10px; }
  .psearch { flex: 0 0 220px; padding: 7px 11px; border-radius: 8px;
    border: 1px solid var(--line); background: #131a26; color: var(--text);
    font-size: 13px; font-family: inherit; }
  .psearch:focus { outline: none; border-color: var(--blue); }
  .teamtoggles { display: flex; gap: 4px; flex-wrap: wrap; }
  .tbtn { padding: 3px; border: 1px solid var(--line); border-radius: 7px;
    background: #131a26; cursor: pointer; line-height: 0; }
  .tbtn img { width: 22px; height: 22px; object-fit: contain; display: block; }
  .tbtn.off { opacity: 0.28; filter: grayscale(1); }
  .tbtn-all { padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
    border: 1px solid var(--line); background: #131a26; color: var(--muted);
    cursor: pointer; font-family: inherit; }
  .tbtn-all:hover { color: var(--text); border-color: #3a4560; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 980px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 18px; min-width: 0; }  /* min-width:0 → 표가 카드 밖으로 넘치지 않음 */
  .card.wide { grid-column: 1 / -1; }
  .card h2 { font-size: 15px; margin: 0 0 4px; }
  .card .hint { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
  .chart-box { position: relative; height: 360px; cursor: pointer; }
  .table-scroll { max-width: 100%; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { padding: 6px 8px; text-align: right; white-space: nowrap; }
  th { color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); }
  td { border-bottom: 1px solid #222a3a; }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
  td.empty { text-align: center; color: var(--muted); }
  .badge { font-size: 15px; margin-right: 6px; }
  tr.row-hidden { display: none; }
  .more { margin-top: 10px; width: 100%; padding: 7px; background: #131a26;
    color: var(--muted); border: 1px solid var(--line); border-radius: 8px;
    font-size: 12px; cursor: pointer; }
  .more:hover { color: var(--text); border-color: #3a4560; }

  /* 산식 카드 */
  .card.wide { grid-column: 1 / -1; }
  .formula-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
  @media (max-width: 980px) { .formula-grid { grid-template-columns: 1fr; } }
  .fblock { background: #131a26; border: 1px solid var(--line); border-radius: 9px; padding: 12px 14px; }
  .fblock h3 { font-size: 13px; margin: 0 0 8px; color: var(--text); }
  .fblock .eq { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
    color: #cfe3ff; background: #0b0e14; border: 1px solid var(--line); border-radius: 6px;
    padding: 8px 10px; line-height: 1.8; overflow-x: auto; white-space: pre-line; }
  .fblock .note { color: var(--muted); font-size: 11.5px; margin-top: 7px; line-height: 1.55; }

  .tip { position: relative; cursor: help; border-bottom: 1px dotted var(--muted); }
  .tip:hover::after { content: attr(data-tip);
    position: absolute; left: 50%; transform: translateX(-50%); bottom: 150%;
    width: min(260px, 78vw); white-space: normal; text-align: left; background: #0b0e14;
    color: var(--text); border: 1px solid var(--line); padding: 9px 11px;
    border-radius: 8px; font-size: 12px; font-weight: 400; line-height: 1.55;
    z-index: 20; box-shadow: 0 8px 24px rgba(0,0,0,.55); }
  /* 모바일 탭 툴팁(플로팅) */
  .tip-pop { position: absolute; z-index: 100; max-width: min(280px, 82vw);
    background: #0b0e14; color: var(--text); border: 1px solid var(--line);
    padding: 9px 11px; border-radius: 8px; font-size: 12px; line-height: 1.55;
    box-shadow: 0 8px 24px rgba(0,0,0,.55); display: none; }
  .pagefoot { color: var(--muted); font-size: 11.5px; line-height: 1.7; text-align: center;
    margin: 32px auto 8px; padding-top: 16px; border-top: 1px solid var(--line); max-width: 720px; }
  .pagefoot b { color: #aab3c5; }

  /* 랜덤 클릭으로 뽑힌 선수 상세 카드 */
  .pick { margin-top: 12px; padding: 12px 13px; background: #0b0e14;
    border: 1px solid var(--line); border-radius: 9px; }
  .pick-info { display: flex; align-items: center; gap: 11px; }
  .pick-info img { width: 34px; height: 34px; object-fit: contain; flex: none; }
  .pick-info .nm { font-weight: 700; font-size: 14px; }
  .pick-info .meta { color: var(--muted); font-size: 12px; line-height: 1.5; }
  .radar-wrap { position: relative; height: 200px; margin-top: 6px; }
  /* 투수 아스널 도넛 */
  .arsenal-wrap { position: relative; height: 200px; margin-top: 10px; }
  .ars-detail { margin-top: 8px; font-size: 12px; color: var(--muted);
    text-align: center; min-height: 34px; line-height: 1.5; }
  .ars-hint { color: #5b647a; font-size: 11px; }

  /* ── 모바일(좁은 폰) 최적화 ── */
  @media (max-width: 560px) {
    body { padding: 12px; }
    h1 { font-size: 18px; }
    .sub { font-size: 12px; }
    .card { padding: 13px; }
    .card h2 { font-size: 14px; }
    table { font-size: 11px; }
    th, td { padding: 5px 5px; }
    /* 스크롤 래퍼가 없는 스크리닝 표는 넘칠 때 자체 가로스크롤 */
    .card > table { display: block; overflow-x: auto; }
    .chart-box { height: 300px; }
    .radar-wrap { height: 180px; }
    .ars-name { width: 64px; }
    .ars-num { width: auto; font-size: 11px; }
    .ars-row { gap: 7px; font-size: 11px; }
    .formula-grid { gap: 10px; }
    .fblock .eq { font-size: 12px; }
    .pick-info img { width: 30px; height: 30px; }
  }
</style>
</head>
<body>

<div class="nav">
  <a class="home" href="../index.html">🏠</a>
  <a href="dashboard.html">📊 팀 전력</a>
  <a class="active" href="players.html">🧢 선수 평가</a>
  <a href="history.html">🏆 역대</a>
  <a href="today.html">🔮 오늘의 경기</a>
  <a href="predictions.html">📈 예측 성적표</a>
  <button id="btnRefresh" class="refresh-btn" title="최신 경기 결과로 다시 계산합니다">🔄 지금 갱신</button>
  <span id="refreshMsg" class="refresh-msg"></span>
</div>
<h1>⚾ KBO __SEASON__ 선수 평가 대시보드</h1>
<div class="sub"><span class="stamp">🕗 최종 갱신 __STAMP__ · <b>__LATEST__ 경기까지 반영</b></span><br>
  투수: 구위×성적 사분면 + 구종 아스널 · 타자: BABIP운 + 5툴 레이더 + FCB 승리기여 · 리그 평균 ERA __LG_ERA__ ·
  <b>지표 이름 호버=공식, 그래프 클릭=랜덤 선수 상세</b></div>

<div class="filterbar">
  <input id="playerSearch" class="psearch" type="search" placeholder="🔍 선수 이름 검색 (예: 오스틴)" autocomplete="off">
  <div class="teamtoggles" id="teamToggles"></div>
  <button id="teamAll" class="tbtn-all">전체</button>
</div>

<div class="grid">

  <div class="card">
    <h2>투수 사분면 — 구위 vs 성적</h2>
    <p class="hint">→구위 좋음 ↑ERA 나쁨. <b>오른쪽 위 = 억울한 투수📈</b>,
      왼쪽 아래 = 시한폭탄⚠️. 점 크기 = 이닝 · <b>클릭 = 랜덤 투수 + 구종 아스널</b></p>
    <div class="chart-box"><canvas id="quadChart"></canvas></div>
    <div class="pick">
      <div class="pick-info" id="pick_quad_info"></div>
      <div class="arsenal-wrap"><canvas id="arsenal_quad"></canvas></div>
      <div class="ars-detail" id="arsenal_detail"></div>
    </div>
  </div>

  <div class="card">
    <h2>ERA − FIP 양극단 — <span class="tip" data-tip="__TIP_ERAFIP__">반등 vs 하락</span></h2>
    <p class="hint"><b style="color:#3ecf8e">초록(위)</b>: ERA≫FIP = 실력보다 억울한 투수 <b>반등 후보📈</b>.
      <b style="color:#ffb454">주황(아래)</b>: ERA≪FIP = 운 좋게 막은 투수 <b>하락 경계📉</b>(곧 나빠질 위험).
      · 위·아래 각 8명. 상세 피해자 목록은 아래 🛡️ 카드 참고.</p>
    <div class="chart-box"><canvas id="gapChart"></canvas></div>
  </div>

  <div class="card">
    <h2>타자 운 산점도 — BABIP vs 생산력</h2>
    <p class="hint">x=BABIP, y=wOBA. 세로선(리그평균)보다 <b>왼쪽=불운💎</b>, 오른쪽=운좋음🫧 ·
      <b>클릭 = 랜덤 타자 + 5툴 레이더</b></p>
    <div class="chart-box"><canvas id="batLuckChart"></canvas></div>
    <div class="pick">
      <div class="pick-info" id="pick_luck_info"></div>
      <div class="radar-wrap"><canvas id="radar_luck"></canvas></div>
    </div>
  </div>

  <div class="card">
    <h2>파워 유형 지도 — Power+ vs HR+</h2>
    <p class="hint">오른쪽 아래=갭 파워🏟️, 왼쪽 위=홈런 스페셜리스트🎰, 오른쪽 위=컴플리트💪 ·
      <b>클릭 = 랜덤 타자 + 5툴 레이더</b></p>
    <div class="chart-box"><canvas id="powerChart"></canvas></div>
    <div class="pick">
      <div class="pick-info" id="pick_power_info"></div>
      <div class="radar-wrap"><canvas id="radar_power"></canvas></div>
    </div>
  </div>

  <div class="card" id="warQuadCard">
    <h2>공격 × 수비 — <span class="tip" data-tip="oWAR: 공격으로 번 승리 기여. dWAR: 수비로 번 승리 기여. Statiz WAR 기준.">oWAR vs dWAR</span>
      <span style="color:var(--muted);font-weight:400">— Statiz</span></h2>
    <p class="hint">→공격 좋음 ↑수비 좋음. <b>오른쪽 위 = 공수겸장</b>, 오른쪽 아래 = 공격형(수비 구멍) ·
      점 크기 = 타석 · <b>클릭 = 랜덤 타자</b>. kbostuff엔 없던 <b>수비 가치</b>가 처음 들어옵니다.
      (PA≥100, 저표본 dWAR 노이즈 컷)</p>
    <div class="chart-box"><canvas id="warQuadChart"></canvas></div>
    <div class="pick"><div class="pick-info" id="pick_war_info"></div></div>
  </div>

  <div class="card wide" id="hotcoldCard">
    <h2><span class="badge">🔥</span>불방망이 · 물방망이 <span style="color:var(--muted);font-weight:400">— 최근 <b id="hcWindow"></b>경기, 평소 대비(Δ)</span></h2>
    <p class="hint"><b>최근 폼</b>입니다. 선택 지표의 최근 값이 시즌 평균보다 얼마나 뜨거운지(Δ)로 줄 세웁니다.
      표본이 작아 <b>운(BABIP)</b>이 많이 섞이니 '실력'이 아니라 <b>'요즘 감'</b>으로 보세요.
      최소 <b id="hcMinPa"></b>타석(최근) · 시즌 80타석 이상.</p>
    <div class="mseg" id="hcMetric"></div>
    <div class="hc-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:8px">
      <div>
        <h3 style="margin:0 0 6px;color:#ff7a45;font-size:14px">🔥 불방망이 <span style="color:var(--muted);font-weight:400">— Δ 상위</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>최근</th><th>최근</th><th>시즌</th><th>Δ</th></tr></thead>
        <tbody id="tb_hcHot"></tbody></table></div>
      </div>
      <div>
        <h3 style="margin:0 0 6px;color:#5aa9ff;font-size:14px">💧 물방망이 <span style="color:var(--muted);font-weight:400">— Δ 하위</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>최근</th><th>최근</th><th>시즌</th><th>Δ</th></tr></thead>
        <tbody id="tb_hcCold"></tbody></table></div>
      </div>
    </div>
  </div>

  <div class="card wide" id="bullpenFormCard">
    <h2><span class="badge">🔥</span>수호신 · 방화범 <span style="color:var(--muted);font-weight:400">— 최근 <b id="bpWindow"></b>등판, 평소 대비(Δ)</span></h2>
    <p class="hint"><b>최근 폼</b>(불펜만, 선발 제외). 선택 지표가 시즌 평균보다 좋아졌으면 수호신, 나빠졌으면 방화 쪽입니다.
      세이브 상황·승계주자 데이터가 없어 <b>진짜 레버리지가 아니라 '최근 실점 억제'</b> 근사이고,
      불펜은 표본이 더 작아 <b>한 경기에 요동</b>칩니다. 최소 <b id="bpMinApp"></b>등판·최근 4이닝 이상.</p>
    <div class="mseg" id="bpMetric"></div>
    <div class="hc-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:8px">
      <div>
        <h3 style="margin:0 0 6px;color:#5aa9ff;font-size:14px">🛡️ 수호신 <span style="color:var(--muted);font-weight:400">— 최근 좋아짐</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>최근</th><th>최근</th><th>시즌</th><th>Δ</th></tr></thead>
        <tbody id="tb_bpHot"></tbody></table></div>
      </div>
      <div>
        <h3 style="margin:0 0 6px;color:#ff5a5a;font-size:14px">🔥 방화범 <span style="color:var(--muted);font-weight:400">— 최근 나빠짐</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>최근</th><th>최근</th><th>시즌</th><th>Δ</th></tr></thead>
        <tbody id="tb_bpCold"></tbody></table></div>
      </div>
    </div>
  </div>

  <div class="card wide" id="starterFormCard">
    <h2><span class="badge">🚀</span>에이스 · 붕괴 <span style="color:var(--muted);font-weight:400">— 최근 <b id="stWindow"></b>선발, 평소 대비(Δ)</span></h2>
    <p class="hint"><b>최근 폼</b>(선발 로테이션만). 선택 지표가 시즌 평균보다 좋아졌으면 에이스, 나빠졌으면 붕괴 쪽입니다.
      최소 <b id="stMinApp"></b>선발·최근 15이닝 이상. 표본이 작아 '최근 흐름'으로 보세요.</p>
    <div class="mseg" id="stMetric"></div>
    <div class="hc-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:8px">
      <div>
        <h3 style="margin:0 0 6px;color:#5aa9ff;font-size:14px">🚀 에이스 <span style="color:var(--muted);font-weight:400">— 최근 좋아짐</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>최근</th><th>최근</th><th>시즌</th><th>Δ</th></tr></thead>
        <tbody id="tb_stHot"></tbody></table></div>
      </div>
      <div>
        <h3 style="margin:0 0 6px;color:#ff5a5a;font-size:14px">💥 붕괴 <span style="color:var(--muted);font-weight:400">— 최근 나빠짐</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>최근</th><th>최근</th><th>시즌</th><th>Δ</th></tr></thead>
        <tbody id="tb_stCold"></tbody></table></div>
      </div>
    </div>
  </div>

  <div class="card" id="streakCard">
    <h2><span class="badge">🔥</span>연속 행진 <span style="color:var(--muted);font-weight:400">— 현재 진행 중</span></h2>
    <p class="hint">지금 이어지고 있는 <b>연속 안타·출루 경기</b> 행진. 타석 없는 경기(대주자 등)는 행진을 끊지 않습니다. 최소 <b id="streakMin"></b>경기.</p>
    <div class="mseg" id="streakToggle"></div>
    <div class="table-scroll" style="margin-top:8px"><table><thead><tr><th>순위</th><th>선수</th><th>팀</th><th>행진</th><th>최근 경기</th></tr></thead>
    <tbody id="tb_streak"></tbody></table></div>
  </div>

  <div class="card wide" id="monthlyCard">
    <h2><span class="badge">🗓️</span>이달의 선수 <span style="color:var(--muted);font-weight:400">— 월별 최고 타자·투수</span></h2>
    <p class="hint">타자 <b>OPS</b>·투수 <b>ERA</b> 순. 부분/현재 달은 표본이 작으니 참고용(타자 25타석·투수 10이닝 이상).</p>
    <div class="mseg" id="monthToggle"></div>
    <div class="hc-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:8px">
      <div>
        <h3 style="margin:0 0 6px;color:#ff7a45;font-size:14px">🏏 이달의 타자 <span style="color:var(--muted);font-weight:400">— OPS</span></h3>
        <div class="table-scroll"><table><thead><tr><th>#</th><th>선수</th><th>팀</th><th>OPS</th><th>타율</th><th>HR·타점</th><th>타석</th></tr></thead>
        <tbody id="tb_moBat"></tbody></table></div>
      </div>
      <div>
        <h3 style="margin:0 0 6px;color:#5aa9ff;font-size:14px">⚾ 이달의 투수 <span style="color:var(--muted);font-weight:400">— ERA</span></h3>
        <div class="table-scroll"><table><thead><tr><th>#</th><th>선수</th><th>팀</th><th>ERA</th><th>이닝</th><th>탈삼진</th></tr></thead>
        <tbody id="tb_moPit"></tbody></table></div>
      </div>
    </div>
  </div>

  <div class="card wide" id="splitsCard">
    <h2><span class="badge">🏟️</span>안방 호랑이 · 원정 강자 <span style="color:var(--muted);font-weight:400">— 홈/원정 OPS 격차</span></h2>
    <p class="hint">홈·원정 각 <b id="splitMin"></b>타석 이상인 타자의 <b>홈 − 원정 OPS 격차</b> 순.</p>
    <div class="hc-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:8px">
      <div>
        <h3 style="margin:0 0 6px;color:#ff7a45;font-size:14px">🏟️ 안방 호랑이 <span style="color:var(--muted);font-weight:400">— 홈에서 강함</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>홈</th><th>원정</th><th>격차</th></tr></thead>
        <tbody id="tb_homeStrong"></tbody></table></div>
      </div>
      <div>
        <h3 style="margin:0 0 6px;color:#5aa9ff;font-size:14px">✈️ 원정 강자 <span style="color:var(--muted);font-weight:400">— 원정에서 강함</span></h3>
        <div class="table-scroll"><table><thead><tr><th>선수</th><th>팀</th><th>홈</th><th>원정</th><th>격차</th></tr></thead>
        <tbody id="tb_awayStrong"></tbody></table></div>
      </div>
    </div>
  </div>

  <div class="card wide" id="bullpenCard">
    <h2><span class="badge">🔥</span>불펜 리더보드 <span style="color:var(--muted);font-weight:400">— 마무리·셋업 · 릴리프 WAR (Statiz)</span></h2>
    <p class="hint">구원 등판이 선발보다 많은 투수(임시선발 1~2번 한 마무리·셋업 포함). 역할은 세이브(마무리)·홀드(셋업)로 판정.
      <b>릴리프 WAR</b> 순. ERA와 FIP가 벌어진 투수는 회귀 후보 — ERA≪FIP는 곧 나빠질 위험, ERA≫FIP는 반등 여지.</p>
    <div class="table-scroll">
    <table><thead><tr><th>선수</th><th>팀</th><th>역할</th><th>WAR</th><th>ERA</th><th>FIP</th><th><span class="tip" data-tip="(탈삼진−볼넷)÷상대타자. 투수 실력 예측력이 가장 높은 지표.">K-BB%</span></th><th>이닝</th><th>세이브</th><th>홀드</th></tr></thead>
    <tbody id="tb_bullpen"></tbody></table>
    </div>
    <button class="more" id="bullpenMore"></button>
  </div>

  <div class="card wide">
    <h2><span class="badge">🔥</span>FCB 승리기여 리더보드 <span style="color:var(--muted);font-weight:400">— kbostuff 고유 지표</span></h2>
    <p class="hint">협조적 게임이론(Shapley value)으로 '득점이 난 순간'의 승리 기여를 공정 분배해 누적.
      승부처에 강했던 타자. <b>단 클러치는 잘 지속되지 않아 미래 예측이 아닌 '서사' 지표입니다.</b></p>
    <div class="table-scroll">
    <table><thead><tr><th>__H_BAT__</th><th>팀</th><th>__H_PA__</th><th>__H_WINS__</th><th>__H_SRC__</th><th>__H_OVR__</th></tr></thead>
    <tbody id="tb_clutch">__T_CLUTCH__</tbody></table>
    </div>
    __M_CLUTCH__
  </div>

  <div class="card">
    <h2><span class="badge">📈</span>억울한 투수 — 반등 후보</h2>
    <p class="hint">구위(K-Stuff+ ≥ 105)는 최상급인데 ERA가 리그 평균보다 나쁜 투수.
      "공은 좋은데 왜 자꾸 맞을까" — 곧 성적이 따라올 확률이 높습니다</p>
    <table><thead><tr><th>__H_PIT__</th><th>팀</th><th>__H_IP__</th><th>__H_ERA__</th><th>__H_FIP__</th><th>__H_STUFF__</th><th>__H_CTRL__</th></tr></thead>
    <tbody id="tb_unlucky">__T_UNLUCKY__</tbody></table>
    __M_UNLUCKY__
  </div>

  <div class="card">
    <h2><span class="badge">⚠️</span>시한폭탄 — 하락 경계</h2>
    <p class="hint">구위(≤ 97)는 평균 이하인데 ERA가 좋은 투수.
      수비와 운이 만든 착시일 수 있어 지속 가능성이 낮습니다</p>
    <table><thead><tr><th>__H_PIT__</th><th>팀</th><th>__H_IP__</th><th>__H_ERA__</th><th>__H_FIP__</th><th>__H_STUFF__</th><th>__H_CTRL__</th></tr></thead>
    <tbody id="tb_timebomb">__T_TIMEBOMB__</tbody></table>
    __M_TIMEBOMB__
  </div>

  <div class="card">
    <h2><span class="badge">🛡️</span>수비/운 피해자 — ERA−FIP &gt; 0.7</h2>
    <p class="hint">삼진·볼넷·홈런만 보면(FIP) 훨씬 잘 던졌는데
      수비 도움을 못 받아 ERA가 부풀려진 투수</p>
    <table><thead><tr><th>__H_PIT__</th><th>팀</th><th>__H_IP__</th><th>__H_ERA__</th><th>__H_FIP__</th><th>__H_STUFF__</th><th>__H_CTRL__</th></tr></thead>
    <tbody id="tb_victim">__T_VICTIM__</tbody></table>
    __M_VICTIM__
  </div>

  <div class="card">
    <h2><span class="badge">💎</span>저평가 타자 — 곧 터질 후보</h2>
    <p class="hint">인플레이 안타 운(BABIP)이 리그 평균보다 크게 낮은 불운한 타자.
      '운' 열이 음수로 클수록 반등 여력이 큽니다</p>
    <table><thead><tr><th>__H_BAT__</th><th>팀</th><th>__H_PA__</th><th>__H_OVR__</th><th>__H_WRC__</th><th>__H_LUCK__</th></tr></thead>
    <tbody id="tb_under">__T_UNDERVALUED__</tbody></table>
    __M_UNDERVALUED__
  </div>

  <div class="card">
    <h2><span class="badge">🫧</span>거품 주의 타자 — 과실현</h2>
    <p class="hint">BABIP가 리그 평균보다 크게 높아 성적 유지가 어려울 수 있는 타자</p>
    <table><thead><tr><th>__H_BAT__</th><th>팀</th><th>__H_PA__</th><th>__H_OVR__</th><th>__H_WRC__</th><th>__H_LUCK__</th></tr></thead>
    <tbody id="tb_bubble">__T_BUBBLE__</tbody></table>
    __M_BUBBLE__
  </div>

  <div class="card">
    <h2><span class="badge">🏟️</span>구장에 갇힌 타자</h2>
    <p class="hint">홈구장 파크팩터가 리그 평균(1.00)보다 낮아 득점이 억제되는 <b>좋은 타자(wRC+ 100↑)</b>.
      wRC+는 이미 구장 보정값이라 실력 평가엔 문제없지만, 홈런·타율 같은 <b>raw 기록</b>은 구장 탓에 눌립니다.
      억제율이 클수록·타격이 좋을수록 손해가 큽니다. <span class="warn">파크팩터는 직전+올 시즌 평균</span>(반시즌 노이즈 완화·신구장 반영).</p>
    <table><thead><tr><th>__H_BAT__</th><th>팀</th><th>__H_PA__</th><th>__H_WRCE__</th><th>__H_PF__</th><th>__H_SUPP__</th></tr></thead>
    <tbody id="tb_park">__T_PARK__</tbody></table>
    __M_PARK__
  </div>

  <div class="card wide">
    <h2>📐 산식 &amp; 방법론</h2>
    <p class="hint">각 지표가 어떻게 계산되는지. 투수 성적은 네이버 경기별 박스스코어를 시즌 합산해 산출합니다.</p>
    <div class="formula-grid">

      <div class="fblock">
        <h3>ERA (평균자책점) · FIP (수비무관 평균자책)</h3>
        <div class="eq">ERA = 9 × 자책점 ÷ 이닝
FIP = (13×피홈런 + 3×(볼넷+사구) − 2×삼진) ÷ 이닝 + C</div>
        <div class="note">FIP는 투수가 온전히 책임지는 사건(홈런·볼넷·사구·삼진)만 반영해 수비·운을 제거.
          상수 C는 '리그 FIP 평균 = 리그 ERA 평균'이 되도록 매 계산 시 맞춤(2026 KBO ≈ 3점대).
          ERA ≫ FIP = 수비·운의 피해자.</div>
      </div>

      <div class="fblock">
        <h3>BABIP · 운</h3>
        <div class="eq">BABIP = 인플레이 안타 ÷ 인플레이 타구
운 = BABIP − 리그평균 BABIP</div>
        <div class="note">인플레이 타구의 안타 비율. 수비 위치·바가지·호수비에 좌우돼 리그평균(~.300)으로
          회귀하는 성질 → 검증된 '운 탐지기'. 음수=불운(반등), 양수=거품.
          단 발 빠른/라인드라이브 타자는 실력으로 높은 BABIP 유지.</div>
      </div>

      <div class="fblock">
        <h3>ERA − FIP 격차 · 구장 억제</h3>
        <div class="eq">수비·운 피해 = ERA − FIP
구장 억제 = (1 − park_factor) × 100%</div>
        <div class="note">ERA−FIP가 클수록 실력 대비 성적이 억울한 투수.
          park_factor는 홈구장의 득점 환경(1.00=평균, &lt;1=투수친화). 억제율이 큰 구장의
          좋은 타자일수록 홈런·장타 raw 기록이 눌린다. ⚠️ 과거 '구장차(wRC+<sub>pure</sub>−<sub>event</sub>)'는
          실제 park_factor와 상관 ≈ 0이라 폐기했다.</div>
      </div>

      <div class="fblock">
        <h3>FCB 승리기여</h3>
        <div class="eq">승리기여 = Σ Shapley(득점 이닝에서의 기여)</div>
        <div class="note">협조적 게임이론의 Shapley value로 '득점이 난 순간'의 승리 기여를 공정 분배해 누적.
          ⚠️ 클러치는 잘 지속되지 않아 미래 예측이 아닌 '지금까지의 서사' 지표.</div>
      </div>

      <div class="fblock" style="grid-column: 1 / -1;">
        <h3>K-Stuff+ / 5툴(+) 지표 — 닫힌 산식이 없는 머신러닝 지표</h3>
        <div class="eq">K-Stuff+ = f(구속, 무브먼트, 회전, 릴리스, 익스텐션 …)  → 100 스케일
5툴+ = 선구(Eye)·컨택(Vision)·타격(Hit)·파워(Power)·주루(Baserunning) 각 트래킹 모델 지수</div>
        <div class="note">K-Stuff+는 공의 <b>물리적 특성만</b>으로 기대 실점가치를 예측하는 모델(MLB Stuff+의 KBO 버전,
          원조는 XGBoost 계열)의 출력이라 대수식이 아닙니다. 결과(안타·홈런)와 위치(제구)는 배제 —
          제구는 K-Location+/K-Control+가 별도. 타자 5툴+·wRC+<sub>pure</sub>도 kbostuff의 트래킹 기반
          모델 산출값(100=리그평균)입니다. ※ 정확한 피처·모델은 kbostuff.app 내부 구현.</div>
      </div>

    </div>
  </div>

</div>

<script>
// ── 🔄 수동 갱신 (serve.py 로컬 서버가 있을 때만 작동) ──
(function () {
  const b = document.getElementById("btnRefresh");
  const m = document.getElementById("refreshMsg");
  if (!b) return;
  b.onclick = async () => {
    try {
      b.disabled = true;
      m.textContent = "갱신 중… 최신 경기 반영 (1~2분)";
      const r = await fetch("/refresh", { method: "POST" });
      if (!r.ok) throw new Error();
    } catch (e) {
      b.disabled = false;
      m.textContent = "⚠️ 갱신은 바탕화면 런처로 열었을 때만 됩니다 (file:// 은 불가)";
      return;
    }
    const poll = setInterval(async () => {
      let s;
      try { s = await (await fetch("/status")).json(); } catch (e) { return; }
      if (s.status === "done") {
        clearInterval(poll);
        m.textContent = "✅ 완료! 새로고침합니다…";
        setTimeout(() => location.reload(), 600);
      } else if (s.status === "error") {
        clearInterval(poll);
        b.disabled = false;
        m.textContent = "❌ 오류: " + (s.message || "파이프라인 실패");
      }
    }, 2000);
  };
})();

const DATA = __DATA__;
Chart.defaults.color = "#8a94a8";
Chart.defaults.borderColor = "#2a3345";
Chart.defaults.font.family = '"Apple SD Gothic Neo","Noto Sans KR",sans-serif';

const rIp = ip => Math.max(3, Math.min(11, ip / 12));
const rPa = pa => Math.max(3, Math.min(11, pa / 45));

// 팀 코드(스포츠투아이 레거시)를 현재 팀명(짧게)으로. OB·SK·HT·WO 등은
// 옛 구단명(OB베어스·SK와이번스·해태·우리)으로 읽혀서 그래프 라벨엔 현재명을 쓴다.
const TEAM_SHORT = { LG:"LG", OB:"두산", HT:"KIA", SS:"삼성", SK:"SSG",
  LT:"롯데", HH:"한화", WO:"키움", NC:"NC", KT:"KT" };
const tShort = t => TEAM_SHORT[t] || t;

// 선택된 선수를 그래프 위에서 흰 링으로 하이라이트하는 플러그인.
// chart._sel(선택 인덱스)를 매 프레임 읽어 그 점 둘레에 원을 그립니다.
const hlPlugin = {
  id: "highlight",
  afterDatasetsDraw(chart) {
    const i = chart._sel;
    if (i == null || i < 0) return;
    const pt = chart.getDatasetMeta(0).data[i];
    const dp = chart.data.datasets[0].data[i];
    // 뷰가 필터로 비워지면 meta/데이터가 어긋날 수 있어 둘 다 방어(없으면 그냥 스킵)
    if (!pt || !dp) return;
    const r = (dp.r || 6) + 7;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath(); ctx.arc(pt.x, pt.y, r, 0, 2 * Math.PI);
    ctx.lineWidth = 3; ctx.strokeStyle = "#ffffff"; ctx.stroke();
    ctx.beginPath(); ctx.arc(pt.x, pt.y, r + 3, 0, 2 * Math.PI);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = (dp.color || "#fff") + "aa";
    ctx.stroke();
    ctx.restore();
  }
};

// 그래프 클릭 → 랜덤 선수(점 클릭 시 그 선수). 5초마다 자동 순환.
// 선택된 선수는 상세 카드 + 그래프 하이라이트로 표시.
// source(전체 선수 배열) + buildPoint(선수→산점도 좌표)를 받아, 팀 토글·검색으로
// 표시 대상(view)을 좁히거나 특정 선수를 포커스할 수 있는 컨트롤러를 돌려줍니다.
function attachRandomPick(chart, infoId, source, buildPoint, fmt, onPick) {
  const info = document.getElementById(infoId);
  let view = source.slice();
  let timer = null;
  function apply() {
    const ds = chart.data.datasets[0];
    ds.data = view.map(buildPoint);
    ds.pointBackgroundColor = view.map(p => p.color + "cc");
    chart.update("none");
  }
  function show(i) {
    if (!view.length) {
      chart._sel = -1;
      info.innerHTML = '<div class="meta" style="padding:6px 0">표시할 선수가 없습니다 (팀/검색 필터 확인)</div>';
      chart.update("none");
      return;
    }
    i = ((i % view.length) + view.length) % view.length;
    chart._sel = i;
    const d = chart.data.datasets[0].data[i];
    info.innerHTML = fmt(d);
    if (onPick) onPick(d);
    chart.update("none");           // 애니메이션 없이 하이라이트만 갱신
  }
  let stopped = false, hoverIdx = -1;
  function rnd() { if (view.length) show(Math.floor(Math.random() * view.length)); }
  function restart() { if (stopped) return; clearInterval(timer); timer = setInterval(rnd, 5000); }
  function pause() { clearInterval(timer); timer = null; }          // 잠시 정지(호버)
  function resume() { if (stopped || timer) return; restart(); }    // 검색고정(stopped) 아니면 재개
  chart.options.onClick = (e, els) => {
    show((els && els.length) ? els[0].index : Math.floor(Math.random() * view.length));
    stopped = false; restart();     // 수동 클릭하면 타이머 리셋
  };
  // 특정 점(선수)에 마우스를 올리는 동안: 그 선수로 고정하고 자동순환 정지
  chart.options.onHover = (e, els) => {
    if (els && els.length) { const i = els[0].index; if (i !== hoverIdx) { hoverIdx = i; show(i); } pause(); }
  };
  chart.canvas.addEventListener("mouseleave", () => { hoverIdx = -1; resume(); });
  apply(); rnd(); restart();
  return {
    setView(v) { stopped = false; view = v; apply(); show(0); restart(); },   // 필터 적용
    focus(pred) {                                            // 특정 선수 선택 + 자동순환 정지(고정)
      const idx = view.findIndex(pred);
      if (idx < 0) return false;
      show(idx); stopped = true; clearInterval(timer); timer = null; return true;
    },
    pause, resume,   // 아스널 등 연동 카드에서 호버 시 정지/재개
  };
}
// 지표 호버 툴팁 — 직관적 항목(홈런·이닝·타석·구속·도루 등)은 제외, 나머지 지수는 산식·의미 표시
const TIP = {
  "ERA":"평균자책점 = 9 × 자책점 ÷ 이닝. 낮을수록 좋음(결과 지표).",
  "FIP":"수비무관 평균자책 = (13×피홈런 + 3×(볼넷+사구) − 2×삼진) ÷ 이닝 + 상수. 수비·운을 걷어낸 투수 본연의 실력.",
  "구위+":"K-Stuff+. 구속·무브먼트·회전 등 공의 물리적 특성만으로 매긴 구위. 100=리그평균.",
  "제구+":"K-Control+. 투구 로케이션(제구) 품질 지수. 100=리그평균.",
  "로케이션+":"K-Location+. 공을 원하는 곳에 넣는 능력. 100=리그평균.",
  "Gap":"구위+ − 로케이션+. 양수면 공은 좋은데 제구가 못 따라옴(제구 성장 시 반등 여지).",
  "한가운데":"한가운데(하트존)에 몰린 투구 비율. 높을수록 실투가 많음.",
  "보더라인":"스트라이크존 경계(에지)에 걸친 투구 비율. 높을수록 제구가 예리함.",
  "BABIP":"인플레이 타구 안타 비율 = (안타−홈런)÷(타수−삼진−홈런+희생플라이). 리그평균≈.300, 편차는 운·수비 영향.",
  "wOBA":"가중 출루율. 각 타격 결과(단타·2루타·볼넷…)에 득점가치 가중치를 매겨 합친 종합 공격 생산력.",
  "종합+":"타격 종합 지수. 선구·컨택·타격·파워·주루를 100 기준으로 합성.",
  "유인구스윙":"존 밖 유인구에 방망이가 나간 비율(Chase%). 낮을수록 선구안이 좋음.",
  "컨택":"스윙 대비 콘택트 성공 비율. 높을수록 헛스윙이 적음.",
  "Power+":"장타 생산력 지수. 100=리그평균.",
  "HR+":"파크팩터 등을 보정한 순수 홈런 파워 지수. 100=리그평균(원시 홈런 수와 다름).",
  "ISO":"순장타율 = 장타율 − 타율. 단타를 뺀 순수 장타력.",
  "WAR":"대체선수 대비 승리기여. 대체 수준 선수보다 팀에 더 벌어준 승수.",
  "oWAR":"공격(타격+주루)으로 번 WAR.",
  "dWAR":"수비(포지션·기여)로 번 WAR.",
  "wRC+":"파크·리그 보정 득점창출력. 100=리그평균, 130=평균보다 30%↑.",
  "BB%":"볼넷 비율 = 볼넷 ÷ 타석. 높을수록 참을성·선구안↑.",
  "K%":"삼진 비율 = 삼진 ÷ 타석. 낮을수록 콘택트↑.",
  "구사":"그 구종을 던진 비율(전체 투구 대비).",
  "구위":"그 구종의 K-Stuff+(물리적 위력). 100=평균.",
  "로케이션":"그 구종의 로케이션+(제구 품질). 100=평균.",
  "헛스윙":"그 구종에 대한 헛스윙 비율(Whiff%). 높을수록 결정구.",
};
function tip(label, key) { key = key || label; const t = TIP[key];
  return t ? '<span class="tip" data-tip="' + t.replace(/"/g, "&quot;") + '">' + label + "</span>" : label; }

function infoHtml(d, lines) {
  return `<img src="${DATA.logos[d.team]}" alt="">
    <div><div class="nm">${d.name} <span class="meta">${d.teamName}</span></div>
    <div class="meta">${lines}</div></div>`;
}

// 5툴 레이더 (선구/컨택/타격/파워/주루) — 선수 구분 없이 초록 통일, 꼭짓점에 수치 표기
const RADAR_GREEN = "#3ecf8e";
// 각 꼭짓점에 값 표시(0/결측은 생략). 중심에서 바깥으로 살짝 밀고 배경 알약으로 가독성 확보
const radarValues = { id: "radarValues", afterDatasetsDraw(ch) {
  const meta = ch.getDatasetMeta(0), ds = ch.data.datasets[0].data;
  const cx = ch.scales.r.xCenter, cy = ch.scales.r.yCenter, ctx = ch.ctx;
  ctx.save(); ctx.font = "700 12px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  meta.data.forEach((pt, i) => {
    const v = ds[i]; if (v == null || v === 0) return;
    const dx = pt.x - cx, dy = pt.y - cy, len = Math.hypot(dx, dy) || 1;
    const ox = pt.x + dx / len * 13, oy = pt.y + dy / len * 13;
    const txt = String(Math.round(v)), w = ctx.measureText(txt).width;
    ctx.fillStyle = "#0e1420dd";
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(ox - w/2 - 4, oy - 9, w + 8, 18, 5); ctx.fill(); }
    else ctx.fillRect(ox - w/2 - 4, oy - 9, w + 8, 18);
    ctx.fillStyle = "#d7f5e6"; ctx.fillText(txt, ox, oy);
  });
  ctx.restore();
}};
function makeRadar(canvasId) {
  return new Chart(document.getElementById(canvasId), {
    type: "radar",
    data: { labels: ["선구", "컨택", "타격", "파워", "주루"],
      datasets: [{ data: [100,100,100,100,100], borderWidth: 2, pointRadius: 3, fill: true,
        borderColor: RADAR_GREEN, backgroundColor: RADAR_GREEN + "26", pointBackgroundColor: RADAR_GREEN }] },
    options: { maintainAspectRatio: false,
      scales: { r: { min: 50, max: 150, ticks: { stepSize: 25, backdropColor: "transparent", color: "#5b647a" },
        grid: { color: "#2a3345" }, angleLines: { color: "#2a3345" },
        pointLabels: { color: "#b8c0d0", font: { size: 12 } } } },
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: c => c.label + " " + c.raw } } } },
    plugins: [radarValues]
  });
}
function updateRadar(radar, d) {
  radar.data.datasets[0].data = [d.eye, d.vision, d.hit, d.power, d.baseR];
  radar.update();   // 색은 초록 고정
}
// ── 투수 아스널 도넛 ──
// 슬라이스 = 구종별 구사율, 가운데 = 투수 유형, 호버/클릭 = 그 구종의 효율
const PITCH_NAME = { FF:"포심", FA:"직구", FT:"투심", SI:"싱커", FC:"커터",
  SL:"슬라이더", ST:"스위퍼", CU:"커브", KC:"너클커브", SV:"슬러브",
  CH:"체인지업", FS:"스플리터", FO:"포크", SC:"스크류", EP:"이퍼스", KN:"너클볼" };
const PITCH_COLOR = { FF:"#e6543c", FA:"#e6543c", FT:"#e8874a", SI:"#e8a04a",
  FC:"#d4a24a", SL:"#4a90d9", ST:"#5bc0de", CU:"#7b6cd9", KC:"#9a6cd9",
  SV:"#6a7ad9", CH:"#45b97c", FS:"#3ba58a", FO:"#3ba58a", SC:"#2fa39a",
  EP:"#8891a5", KN:"#8891a5" };
const _fallbackPal = ["#4a90d9","#e6543c","#45b97c","#e8a04a","#7b6cd9","#5bc0de","#d4a24a"];
function pitchName(a) { return PITCH_NAME[a.code] || a.group || a.code; }
function pitchColor(a, i) { return PITCH_COLOR[a.code] || _fallbackPal[i % _fallbackPal.length]; }

// 도넛 가운데에 투수 유형을 그리는 플러그인
const arsenalCenter = { id: "arsenalCenter", afterDraw(ch) {
  const t = ch.$centerText; if (!t) return;
  const { ctx, chartArea: { left, right, top, bottom } } = ch;
  const cx = (left + right) / 2, cy = (top + bottom) / 2;
  ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillStyle = "#5b647a"; ctx.font = "10px sans-serif";
  ctx.fillText("유형", cx, cy - 16);
  ctx.fillStyle = "#e8ecf3"; ctx.font = "600 13px sans-serif";
  ctx.fillText(t, cx, cy + 2);
  ctx.restore();
}};

let arsenalChart = null;
function renderArsenal(canvasId, detailId, d) {
  const det = document.getElementById(detailId);
  const A = (d.arsenal || []).slice().sort((a, b) => b.usage - a.usage);
  if (!A.length) {
    if (arsenalChart) { arsenalChart.destroy(); arsenalChart = null; }
    if (det) det.innerHTML = "";
    return;
  }
  const colors = A.map((a, i) => pitchColor(a, i));
  const setDetail = i => {
    const a = A[i];
    det.innerHTML =
      `<b style="color:${colors[i]}">${pitchName(a)}</b> · ${tip("구사")} <b>${a.usage}%</b>`
      + ` · ${tip("구위")} ${a.stuff} · ${tip("로케이션")} ${a.loc} · ${tip("헛스윙")} ${a.whiff}% · ${tip("한가운데")} ${a.heart}%`
      + (a.speed ? ` · ${a.speed}km/h` : "")
      + `<br><span class="ars-hint">구종 조각에 마우스를 올리면(모바일은 탭) 그 구종의 효율이 바뀝니다</span>`;
  };
  if (!arsenalChart) {
    arsenalChart = new Chart(document.getElementById(canvasId), {
      type: "doughnut",
      data: { labels: A.map(pitchName),
        datasets: [{ data: A.map(a => a.usage), backgroundColor: colors,
          borderColor: "#0e1420", borderWidth: 2, hoverOffset: 8 }] },
      options: { maintainAspectRatio: false, cutout: "60%",
        // 5초마다 자동 순환하므로 긴 부채꼴 애니메이션은 산만 → 짧게
        animation: { duration: 300 },
        onHover: (e, els) => { if (els && els.length) setDetail(els[0].index); },
        onClick: (e, els) => { if (els && els.length) setDetail(els[0].index); },
        plugins: {
          legend: { position: "right",
            labels: { color: "#b8c0d0", font: { size: 11 }, boxWidth: 12, padding: 8 } },
          tooltip: { callbacks: { label: c => {
            const a = A[c.dataIndex];
            // 제목에 이미 구종명이 뜨므로 여기선 생략(중복 방지)
            return `구사 ${a.usage}% · 구위 ${a.stuff} · 헛스윙 ${a.whiff}%`; } } }
        } },
      plugins: [arsenalCenter]
    });
  } else {
    arsenalChart.data.labels = A.map(pitchName);
    arsenalChart.data.datasets[0].data = A.map(a => a.usage);
    arsenalChart.data.datasets[0].backgroundColor = colors;
  }
  // 가운데 유형: 괄호 설명은 떼고 핵심만 (예: "📈 억울한 투수")
  arsenalChart.$centerText = d.type ? d.type.replace(/\s*\(.*\)\s*/, "") : "";
  arsenalChart.update();
  setDetail(0);   // 기본: 주무기(구사율 1위)
}

// ── ① 투수 사분면 ──
const quad = new Chart(document.getElementById("quadChart"), {
  type: "scatter",
  data: { datasets: [{
    data: DATA.pitchers.map(p => ({x: p.stuff, y: p.era, r: rIp(p.ip), ...p})),
    pointBackgroundColor: DATA.pitchers.map(p => p.color + "cc"),
    pointRadius: c => c.raw ? c.raw.r : 0, pointHoverRadius: c => (c.raw ? c.raw.r : 0) + 3 }]},
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
      `${c.raw.name}(${tShort(c.raw.team)}) 구위+ ${c.raw.stuff} / ERA ${c.raw.era} / FIP ${c.raw.fip} — ${c.raw.type}` }}},
    scales: { x: { title: { display: true, text: "K-Stuff+ (구위, 100=평균)" }, grid: { color: "#222a3a" } },
      y: { title: { display: true, text: "ERA (높을수록 나쁨)" }, grid: { color: "#222a3a" } } } },
  plugins: [hlPlugin, { id: "quadLines", afterDraw(ch) {
    const {ctx, chartArea: a, scales: {x, y}} = ch; ctx.save();
    ctx.strokeStyle = "#3a4560"; ctx.setLineDash([5,5]); ctx.lineWidth = 1;
    const ye = y.getPixelForValue(DATA.lgEra);
    ctx.beginPath(); ctx.moveTo(a.left, ye); ctx.lineTo(a.right, ye); ctx.stroke();
    [DATA.stuffHigh, DATA.stuffLow].forEach(v => { const px = x.getPixelForValue(v);
      if (px > a.left && px < a.right) { ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke(); } });
    ctx.fillStyle = "#8a94a8"; ctx.font = "11px sans-serif";
    ctx.fillText("리그 평균 ERA " + DATA.lgEra, a.left + 6, ye - 6); ctx.restore(); }}]
});
const quadCtl = attachRandomPick(quad, "pick_quad_info",
  DATA.pitchers, p => ({x: p.stuff, y: p.era, r: rIp(p.ip), ...p}),
  d => infoHtml(d, `이닝 ${d.ip} · ${tip("ERA")} ${d.era} · ${tip("FIP")} ${d.fip} · ${tip("구위+")} ${d.stuff} · ${tip("제구+")} ${d.control} · 평균구속 ${d.speed} · ${d.type}`
    + (d.loc != null ? `<br><span style="color:var(--muted)">${tip("로케이션+")} ${d.loc} · ${tip("Gap")}(구위−로케이션) ${d.locGap>0?'+':''}${d.locGap} · ${tip("한가운데")} ${d.heart}% · ${tip("보더라인")} ${d.edge}% — ${d.locType}</span>` : "")),
  d => renderArsenal("arsenal_quad", "arsenal_detail", d));
// 아스널(구종) 도넛에 마우스를 올리는 동안엔 투수 자동순환을 멈춰 구종을 살펴볼 수 있게
(function freezeOnArsenal() {
  const c = document.getElementById("arsenal_quad");
  if (!c) return;
  c.addEventListener("mouseenter", () => quadCtl.pause());
  c.addEventListener("mouseleave", () => quadCtl.resume());
})();

// ── ② ERA-FIP 양극단 (상위=억울한 반등후보, 하위=운좋은 하락경계) ──
const _byGap = [...DATA.pitchers].sort((a,b) => b.gap - a.gap);
const NEND = 8;
const victims = [..._byGap.slice(0, NEND), ..._byGap.slice(-NEND)];
new Chart(document.getElementById("gapChart"), {
  type: "bar",
  data: { labels: victims.map(p => `${p.name}(${tShort(p.team)})`),
    datasets: [{ data: victims.map(p => p.gap),
      backgroundColor: victims.map(p => p.gap > 0 ? "#3ecf8e" : "#ffb454"), borderRadius: 4 }]},
  options: { indexAxis: "y", maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => {
      const p = victims[c.dataIndex];
      const tag = p.gap > 0 ? "억울(반등 후보)" : "운 좋음(하락 경계)";
      return `ERA ${p.era} − FIP ${p.fip} = ${p.gap > 0 ? "+" : ""}${p.gap} — ${tag}`; }}}},
    scales: { x: { grid: { color: "#222a3a" }, title: { display: true, text: "← 하락 경계   ERA − FIP   반등 후보 →" } }, y: { grid: { display: false } } } }
});

// ── ③ 타자 운 산점도 (BABIP vs wOBA) ──
const bats = DATA.batters.filter(b => b.woba != null && b.babip != null);
const batLuck = new Chart(document.getElementById("batLuckChart"), {
  type: "scatter",
  data: { datasets: [{
    data: bats.map(b => ({x: b.babip, y: b.woba, r: rPa(b.pa), ...b})),
    pointBackgroundColor: bats.map(b => b.color + "cc"),
    pointRadius: c => c.raw ? c.raw.r : 0, pointHoverRadius: c => (c.raw ? c.raw.r : 0) + 3 }]},
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
      `${c.raw.name}(${tShort(c.raw.team)}) BABIP ${c.raw.x.toFixed(3)} / wOBA ${c.raw.y.toFixed(3)} / ${c.raw.pa}타석` }}},
    scales: { x: { title: { display: true, text: "BABIP (인플레이 안타 비율)" }, grid: { color: "#222a3a" } },
      y: { title: { display: true, text: "wOBA (인플레이 생산력)" }, grid: { color: "#222a3a" } } } },
  plugins: [hlPlugin, { id: "babipLine", afterDraw(ch) {
    const {ctx, chartArea: a, scales: {x}} = ch; if (DATA.lgBabip == null) return;
    const px = x.getPixelForValue(DATA.lgBabip); if (px < a.left || px > a.right) return;
    ctx.save(); ctx.strokeStyle = "#3a4560"; ctx.setLineDash([5,5]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
    ctx.fillStyle = "#8a94a8"; ctx.font = "11px sans-serif";
    ctx.fillText("리그 BABIP " + DATA.lgBabip, px + 5, a.top + 12); ctx.restore(); }}]
});
const radarLuck = makeRadar("radar_luck");
const batLuckCtl = attachRandomPick(batLuck, "pick_luck_info",
  bats, b => ({x: b.babip, y: b.woba, r: rPa(b.pa), ...b}),
  d => infoHtml(d, `타석 ${d.pa} · ${tip("BABIP")} ${d.babip} · ${tip("wOBA")} ${d.woba} · ${tip("종합+")} ${d.overall} · ${tip("유인구스윙")} ${d.chase}% · ${tip("컨택")} ${d.contact}% · ${d.luckType}`),
  d => updateRadar(radarLuck, d));

// ── ④ 파워 유형 지도 ──
const pw = DATA.batters.filter(b => b.power != null && b.hr != null);
const power = new Chart(document.getElementById("powerChart"), {
  type: "scatter",
  data: { datasets: [{
    data: pw.map(b => ({x: b.power, y: b.hr, r: rPa(b.pa), ...b})),
    pointBackgroundColor: pw.map(b => b.color + "cc"),
    pointRadius: c => c.raw ? c.raw.r : 0, pointHoverRadius: c => (c.raw ? c.raw.r : 0) + 3 }]},
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
      `${c.raw.name}(${tShort(c.raw.team)}) Power+ ${c.raw.x} / HR+ ${c.raw.y}` }}},
    scales: { x: { title: { display: true, text: "Power+ (장타 생산력)" }, grid: { color: "#222a3a" } },
      y: { title: { display: true, text: "HR+ (순수 홈런 파워)" }, grid: { color: "#222a3a" } } } },
  plugins: [hlPlugin, { id: "centerLines", afterDraw(ch) {
    const {ctx, chartArea: a, scales: {x, y}} = ch; ctx.save();
    ctx.strokeStyle = "#3a4560"; ctx.setLineDash([5,5]); ctx.lineWidth = 1;
    const cx = x.getPixelForValue(100), cy = y.getPixelForValue(100);
    if (cx > a.left && cx < a.right) { ctx.beginPath(); ctx.moveTo(cx, a.top); ctx.lineTo(cx, a.bottom); ctx.stroke(); }
    if (cy > a.top && cy < a.bottom) { ctx.beginPath(); ctx.moveTo(a.left, cy); ctx.lineTo(a.right, cy); ctx.stroke(); }
    ctx.restore(); }}]
});
const radarPower = makeRadar("radar_power");
const powerCtl = attachRandomPick(power, "pick_power_info",
  pw, b => ({x: b.power, y: b.hr, r: rPa(b.pa), ...b}),
  d => infoHtml(d, `타석 ${d.pa} · ${tip("Power+")} ${d.power} · ${tip("HR+")} ${d.hr} · ${tip("ISO")} ${d.iso} · ${d.powerType}`),
  d => updateRadar(radarPower, d));

// ── ⑤ 공격 × 수비 (oWAR vs dWAR, Statiz) ──
const sbats = DATA.statizBatters || [];
let warQuadCtl = null;
if (!sbats.length) {
  const c = document.getElementById("warQuadCard"); if (c) c.style.display = "none";
} else {
  const rWar = b => 5 + Math.min(11, Math.max(0, (b.pa - 100) / 45));
  const warQuad = new Chart(document.getElementById("warQuadChart"), {
    type: "scatter",
    data: { datasets: [{
      data: sbats.map(b => ({x: b.owar, y: b.dwar, r: rWar(b), ...b})),
      pointBackgroundColor: sbats.map(b => b.color + "cc"),
      pointRadius: c => c.raw ? c.raw.r : 0, pointHoverRadius: c => (c.raw ? c.raw.r : 0) + 3 }]},
    options: { maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
        `${c.raw.name}(${tShort(c.raw.team)} ${c.raw.pos}) oWAR ${c.raw.x} / dWAR ${c.raw.y} / WAR ${c.raw.war}` }}},
      scales: { x: { title: { display: true, text: "oWAR (공격 기여)" }, grid: { color: "#222a3a" } },
        y: { title: { display: true, text: "dWAR (수비 기여)" }, grid: { color: "#222a3a" } } } },
    plugins: [hlPlugin, { id: "warZero", afterDraw(ch) {
      const {ctx, chartArea: a, scales: {y}} = ch; const py = y.getPixelForValue(0);
      if (py < a.top || py > a.bottom) return;
      ctx.save(); ctx.strokeStyle = "#3a4560"; ctx.setLineDash([5,5]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
      ctx.fillStyle = "#8a94a8"; ctx.font = "11px sans-serif";
      ctx.fillText("수비 평균(dWAR 0)", a.left + 5, py - 5); ctx.restore(); }}]
  });
  warQuadCtl = attachRandomPick(warQuad, "pick_war_info",
    sbats, b => ({x: b.owar, y: b.dwar, r: rWar(b), ...b}),
    d => infoHtml(d, `${d.pos} · ${d.pa}타석 · ${tip("WAR")} ${d.war} (공격 ${tip("oWAR")} ${d.owar} + 수비 ${tip("dWAR")} ${d.dwar}) · ${tip("wRC+")} ${d.wrcplus}`
      + ` — ${d.owar >= 2 && d.dwar >= 0.3 ? "💎 공수겸장" : d.owar >= 2 && d.dwar <= -0.3 ? "🏏 공격형(수비 구멍)"
          : d.owar >= 2 ? "🏏 공격형" : d.dwar >= 0.5 ? "🧤 수비형" : "➖ 평범"}`
      + `<br><span style="color:var(--muted)">규율 ${tip("BB%")} ${d.bbpct} · ${tip("K%")} ${d.kpct} · ${tip("ISO")} ${d.iso} · 주루 도루 ${d.sb}(실패 ${d.cs}) · 병살 ${d.gdp}</span>`));
}

// ── 최근 폼 리더보드(지표 토글): 물오른/식은 방망이 · 필승/방화 불펜 ──
(function recentFormBoards() {
  const TOP = 15;
  const LOGO = t => DATA.logos[t]
    ? `<img src="${DATA.logos[t]}" alt="" style="height:14px;vertical-align:-2px;margin-right:3px">` : "";
  // 지표 정의: low=true면 낮을수록 좋음(ERA 등). calc(합계객체)→값.
  const BAT = {
    ops: { label: "OPS", dec: 3, low: false, calc: s => { const pa = s.ab + s.bb; const obp = pa ? (s.h + s.bb) / pa : 0; return obp + (s.ab ? s.tb / s.ab : 0); } },
    avg: { label: "타율", dec: 3, low: false, calc: s => s.ab ? s.h / s.ab : 0 },
    slg: { label: "장타율", dec: 3, low: false, calc: s => s.ab ? s.tb / s.ab : 0 },
  };
  const PIT = {
    era: { label: "ERA", dec: 2, low: true, calc: s => s.outs ? s.er * 27 / s.outs : 0 },
    ra9: { label: "RA9", dec: 2, low: true, calc: s => s.outs ? s.r * 27 / s.outs : 0 },
    whip: { label: "WHIP", dec: 2, low: true, calc: s => s.outs ? (s.bb + s.h) * 3 / s.outs : 0 },
  };

  function formBoard(pool, metrics, ids, meta) {
    const card = document.getElementById(ids.card);
    if (!pool || !pool.length) { if (card) card.style.display = "none"; return; }
    const wEl = document.getElementById(ids.win); if (wEl) wEl.textContent = meta.window;
    const mEl = document.getElementById(ids.min); if (mEl) mEl.textContent = meta.min;
    const seg = document.getElementById(ids.seg);
    const keys = Object.keys(metrics);
    let key = keys[0];

    function draw() {
      const m = metrics[key];
      seg.innerHTML = keys.map(k => {
        const on = k === key;
        return `<button data-k="${k}" style="padding:3px 11px;margin:0 6px 0 0;border-radius:6px;`
          + `border:1px solid ${on ? '#3ecf8e' : '#2a3345'};background:${on ? '#173a2b' : 'transparent'};`
          + `color:${on ? '#3ecf8e' : '#8a94a8'};font-size:12px;cursor:pointer">${metrics[k].label}</button>`;
      }).join("");
      const scored = pool.map(r => {
        const rv = m.calc(r.recent), sv = m.calc(r.season);
        const imp = m.low ? (sv - rv) : (rv - sv);   // + = 좋아짐
        return { r, rv, sv, imp };
      }).filter(x => isFinite(x.imp));
      const dcol = i => i >= 0 ? "#3ecf8e" : "#e0555f";
      const rowHTML = x => `<tr><td>${LOGO(x.r.team)}${x.r.name}</td><td>${x.r.teamName}</td>`
        + `<td style="color:#8a94a8">${meta.sample(x.r.recent)}</td>`
        + `<td><b>${x.rv.toFixed(m.dec)}</b></td>`
        + `<td style="color:#8a94a8">${x.sv.toFixed(m.dec)}</td>`
        + `<td style="color:${dcol(x.imp)};font-weight:600">${x.imp >= 0 ? "+" : ""}${x.imp.toFixed(m.dec)}</td></tr>`;
      document.getElementById(ids.hot).innerHTML =
        scored.slice().sort((a, b) => b.imp - a.imp).slice(0, TOP).map(rowHTML).join("");
      document.getElementById(ids.cold).innerHTML =
        scored.slice().sort((a, b) => a.imp - b.imp).slice(0, TOP).map(rowHTML).join("");
    }
    seg.onclick = e => { const b = e.target.closest("button"); if (!b) return; key = b.dataset.k; draw(); };
    draw();
  }

  const hc = DATA.hotcold || { players: [] };
  formBoard(hc.players, BAT,
    { card: "hotcoldCard", seg: "hcMetric", hot: "tb_hcHot", cold: "tb_hcCold", win: "hcWindow", min: "hcMinPa" },
    { window: hc.window, min: hc.minPa, sample: r => `${r.g}G·${r.ab + r.bb}타석` });

  const bp = DATA.bullpenForm || { players: [] };
  formBoard(bp.players, PIT,
    { card: "bullpenFormCard", seg: "bpMetric", hot: "tb_bpHot", cold: "tb_bpCold", win: "bpWindow", min: "bpMinApp" },
    { window: bp.window, min: bp.minApp, sample: r => `${r.g}등판·${(r.outs / 3).toFixed(1)}이닝` });

  const st = DATA.starterForm || { players: [] };
  formBoard(st.players, PIT,
    { card: "starterFormCard", seg: "stMetric", hot: "tb_stHot", cold: "tb_stCold", win: "stWindow", min: "stMinApp" },
    { window: st.window, min: st.minApp, sample: r => `${r.g}선발·${(r.outs / 3).toFixed(1)}이닝` });
})();

// ── 연속 안타·출루 행진 (현재 진행 중) ──
(function streakBoard() {
  const S = DATA.streaks || { hit: [], onbase: [] };
  const card = document.getElementById("streakCard");
  if (!S.hit.length && !S.onbase.length) { if (card) card.style.display = "none"; return; }
  const mEl = document.getElementById("streakMin"); if (mEl) mEl.textContent = S.minStreak;
  const seg = document.getElementById("streakToggle");
  const LOGO = t => DATA.logos[t]
    ? `<img src="${DATA.logos[t]}" alt="" style="height:14px;vertical-align:-2px;margin-right:3px">` : "";
  const modes = { hit: "안타 행진", onbase: "출루 행진" };
  let key = "hit";
  function draw() {
    seg.innerHTML = Object.entries(modes).map(([k, lab]) => {
      const on = k === key;
      return `<button data-k="${k}" style="padding:3px 11px;margin:0 6px 0 0;border-radius:6px;`
        + `border:1px solid ${on ? '#3ecf8e' : '#2a3345'};background:${on ? '#173a2b' : 'transparent'};`
        + `color:${on ? '#3ecf8e' : '#8a94a8'};font-size:12px;cursor:pointer">${lab}</button>`;
    }).join("");
    document.getElementById("tb_streak").innerHTML = (S[key] || []).map((r, i) =>
      `<tr><td>${i + 1}</td><td>${LOGO(r.team)}${r.name}</td><td>${r.teamName}</td>`
      + `<td style="color:#ff7a45;font-weight:700">${r.streak}경기</td>`
      + `<td style="color:#8a94a8">${r.last}</td></tr>`).join("");
  }
  seg.onclick = e => { const b = e.target.closest("button"); if (!b) return; key = b.dataset.k; draw(); };
  draw();
})();

// ── 이달의 선수 (월별 최고 타자·투수) ──
(function monthlyBoard() {
  const M = DATA.monthly || { months: [] };
  const card = document.getElementById("monthlyCard");
  if (!M.months || !M.months.length) { if (card) card.style.display = "none"; return; }
  const LOGO = t => DATA.logos[t]
    ? `<img src="${DATA.logos[t]}" alt="" style="height:14px;vertical-align:-2px;margin-right:3px">` : "";
  const seg = document.getElementById("monthToggle");
  let key = M.months[M.months.length - 1];   // 최신 달 기본
  function draw() {
    seg.innerHTML = M.months.map(m => {
      const on = m === key, lab = m.slice(5) + "월";
      return `<button data-k="${m}" style="padding:3px 11px;margin:0 6px 0 0;border-radius:6px;`
        + `border:1px solid ${on ? '#3ecf8e' : '#2a3345'};background:${on ? '#173a2b' : 'transparent'};`
        + `color:${on ? '#3ecf8e' : '#8a94a8'};font-size:12px;cursor:pointer">${lab}</button>`;
    }).join("");
    document.getElementById("tb_moBat").innerHTML = (M.batters[key] || []).map((r, i) =>
      `<tr><td>${i + 1}</td><td>${LOGO(r.team)}${r.name}</td><td>${r.teamName}</td>`
      + `<td><b>${r.ops.toFixed(3)}</b></td><td style="color:#8a94a8">${r.avg.toFixed(3)}</td>`
      + `<td>${r.hr}·${r.rbi}</td><td style="color:#8a94a8">${r.pa}</td></tr>`).join("");
    document.getElementById("tb_moPit").innerHTML = (M.pitchers[key] || []).map((r, i) =>
      `<tr><td>${i + 1}</td><td>${LOGO(r.team)}${r.name}</td><td>${r.teamName}</td>`
      + `<td><b>${r.era.toFixed(2)}</b></td><td style="color:#8a94a8">${r.ip.toFixed(1)}</td>`
      + `<td>${r.so}</td></tr>`).join("");
  }
  seg.onclick = e => { const b = e.target.closest("button"); if (!b) return; key = b.dataset.k; draw(); };
  draw();
})();

// ── 안방 호랑이 · 원정 강자 (홈/원정 OPS 격차) ──
(function splitsBoard() {
  const S = DATA.splits || { homeStrong: [], awayStrong: [] };
  const card = document.getElementById("splitsCard");
  if (!S.homeStrong.length && !S.awayStrong.length) { if (card) card.style.display = "none"; return; }
  const mEl = document.getElementById("splitMin"); if (mEl) mEl.textContent = S.minPa;
  const LOGO = t => DATA.logos[t]
    ? `<img src="${DATA.logos[t]}" alt="" style="height:14px;vertical-align:-2px;margin-right:3px">` : "";
  const row = r => `<tr><td>${LOGO(r.team)}${r.name}</td><td>${r.teamName}</td>`
    + `<td><b>${r.homeOps.toFixed(3)}</b></td><td>${r.awayOps.toFixed(3)}</td>`
    + `<td style="color:${r.gap >= 0 ? '#ff7a45' : '#5aa9ff'};font-weight:600">${r.gap >= 0 ? '+' : ''}${r.gap.toFixed(3)}</td></tr>`;
  document.getElementById("tb_homeStrong").innerHTML = S.homeStrong.map(row).join("");
  document.getElementById("tb_awayStrong").innerHTML = S.awayStrong.map(row).join("");
})();

// ── ⑥ 불펜 리더보드 (Statiz) ──
(function bullpenBoard() {
  const rel = (DATA.relievers || []).slice().sort((a, b) => b.war - a.war);
  const card = document.getElementById("bullpenCard");
  if (!rel.length) { if (card) card.style.display = "none"; return; }
  const role = r => (r.sv >= 5 || (r.sv >= r.hd && r.sv >= 3)) ? "🔒 마무리"
    : r.hd >= 5 ? "🅢 셋업" : "불펜";
  const COLLAPSE = 12;
  const tb = document.getElementById("tb_bullpen");
  tb.innerHTML = rel.map((r, i) => {
    const hide = i >= COLLAPSE ? ' class="row-hidden"' : "";
    const gap = r.era - r.fip;
    const flag = gap <= -0.5 ? ' <span class="neg" title="ERA≪FIP 회귀 경계">🔴</span>'
      : gap >= 0.5 ? ' <span class="pos" title="ERA≫FIP 반등 여지">🔵</span>' : "";
    return `<tr${hide}><td>${r.name}${flag}</td><td>${r.teamName}</td><td>${role(r)}</td>`
      + `<td>${r.war.toFixed(2)}</td><td>${r.era.toFixed(2)}</td><td>${r.fip.toFixed(2)}</td>`
      + `<td>${r.kbb != null ? r.kbb.toFixed(1) + "%" : "-"}</td>`
      + `<td>${r.ip.toFixed(1)}</td><td>${r.sv || "-"}</td><td>${r.hd || "-"}</td></tr>`;
  }).join("");
  const more = document.getElementById("bullpenMore");
  if (rel.length > COLLAPSE) {
    more.textContent = `＋ 더 보기 (${rel.length - COLLAPSE}명 더)`;
    const hidden = tb.querySelectorAll("tr.row-hidden");
    more.onclick = () => {
      const opening = hidden[0].style.display !== "table-row";
      hidden.forEach(tr => { tr.style.display = opening ? "table-row" : "none"; });
      more.textContent = opening ? "− 접기" : `＋ 더 보기 (${hidden.length}명 더)`;
    };
  } else { more.style.display = "none"; }
})();

// ── 팀 토글 + 선수 검색 + 링크 포커스 ──────────────────────
(function setupFilters() {
  // 팀 목록 (로고맵 순서 유지)
  const ALL = Object.keys(DATA.logos).filter(t =>
    DATA.pitchers.some(p => p.team === t) || DATA.batters.some(b => b.team === t));
  const enabled = new Set(ALL);
  let q = "";
  const togglesEl = document.getElementById("teamToggles");
  ALL.forEach(t => {
    const b = document.createElement("button");
    b.className = "tbtn"; b.title = t;
    b.innerHTML = `<img src="${DATA.logos[t]}" alt="${t}">`;
    b.onclick = () => {
      enabled.has(t) ? enabled.delete(t) : enabled.add(t);
      b.classList.toggle("off", !enabled.has(t));
      apply();
    };
    togglesEl.appendChild(b);
  });
  document.getElementById("teamAll").onclick = () => {
    ALL.forEach(t => enabled.add(t));
    [...togglesEl.children].forEach(c => c.classList.remove("off"));
    document.getElementById("playerSearch").value = ""; q = "";
    apply();
  };
  const se = document.getElementById("playerSearch");
  se.addEventListener("input", () => { q = se.value.trim(); apply(); });

  const ok = p => enabled.has(p.team) && (q === "" || p.name.includes(q));
  function apply() {
    const qv = DATA.pitchers.filter(ok);
    const bv = bats.filter(ok);
    const pv = pw.filter(ok);
    const wv = (DATA.statizBatters || []).filter(ok);
    quadCtl.setView(qv);
    batLuckCtl.setView(bv);
    powerCtl.setView(pv);
    if (warQuadCtl) warQuadCtl.setView(wv);
    // 검색 중이면 첫 매치를 포커스(자동순환 정지)해 바로 보이게
    if (q) {
      if (qv.length) quadCtl.focus(p => p.name.includes(q));
      if (bv.length) batLuckCtl.focus(p => p.name.includes(q));
      if (pv.length) powerCtl.focus(p => p.name.includes(q));
      if (warQuadCtl && wv.length) warQuadCtl.focus(p => p.name.includes(q));
    }
  }

  // 팀 대시보드 로테이션 카드에서 넘어온 특정 투수 포커스 (?p=pcode)
  const pc = new URLSearchParams(location.search).get("p");
  if (pc && quadCtl.focus(p => p.pcode === pc)) {
    const card = document.getElementById("quadChart").closest(".card");
    if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();

// ── '더 보기' 토글 (data-tb 있는 스크리닝 테이블만; 불펜 버튼은 자체 핸들러) ──
document.querySelectorAll(".more[data-tb]").forEach(btn => {
  const tb = document.getElementById(btn.dataset.tb);
  if (!tb) return;
  const hidden = tb.querySelectorAll("tr.row-hidden");
  btn.addEventListener("click", () => {
    const opening = hidden[0].style.display !== "table-row";
    hidden.forEach(tr => { tr.style.display = opening ? "table-row" : "none"; });
    btn.textContent = opening ? "− 접기" : `＋ 더 보기 (${hidden.length}명 더)`;
  });
});

// 모바일(터치): 지표를 탭하면 정의 표시. 데스크톱(hover 가능)은 CSS 호버 유지.
(function tapTips(){
  if (!window.matchMedia || !matchMedia('(hover: none)').matches) return;
  let pop = null;
  document.addEventListener('click', function(e){
    const t = e.target.closest('[data-tip]');
    if (!t || !t.getAttribute('data-tip')) { if (pop) pop.style.display='none'; return; }
    if (!pop) { pop = document.createElement('div'); pop.className='tip-pop'; document.body.appendChild(pop); }
    if (pop._for === t && pop.style.display==='block') { pop.style.display='none'; pop._for=null; return; }
    pop._for = t; pop.textContent = t.getAttribute('data-tip'); pop.style.display='block';
    const r = t.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(window.innerWidth-8-pop.offsetWidth, r.left+r.width/2-pop.offsetWidth/2))+'px';
    pop.style.top = (window.scrollY + r.bottom + 6)+'px';
  }, true);
})();
</script>
<footer class="pagefoot">
  <b>자료 출처</b> · 네이버 야구(경기 기록) · KBO Talent(kbostuff.app, 트래킹 지표) · 스태티즈 Statiz(WAR·세부 기록)<br>
  취미·학습 목적의 <b>개인 세이버매트릭스 프로젝트</b>입니다. 상업적 이용을 하지 않으며, 상업적으로 이용할 수 없습니다. 데이터 권리는 각 출처에 있습니다.
</footer>
</body>
</html>
"""


def _inject_headers(html: str) -> str:
    reps = {
        "__H_PIT__": _tip("투수", "투수"), "__H_IP__": _tip("이닝", "이닝"),
        "__H_ERA__": _tip("ERA", "ERA"), "__H_FIP__": _tip("FIP", "FIP"),
        "__H_STUFF__": _tip("구위+", "구위+"), "__H_CTRL__": _tip("제구+", "제구+"),
        "__H_BAT__": _tip("타자", "타자"), "__H_PA__": _tip("타석", "타석"),
        "__H_OVR__": _tip("종합+", "종합+"), "__H_WRC__": _tip("wRC+순수", "wRC+순수"),
        "__H_WRCE__": _tip("wRC+", "wRC+실제"), "__H_PF__": _tip("홈 파크팩터", "파크팩터"),
        "__H_SUPP__": _tip("억제(중립대비)", "구장억제"),
        "__H_LUCK__": _tip("운", "운"),
        "__H_WINS__": _tip("승리기여", "승리기여"), "__H_SRC__": _tip("경기당SRC", "경기당SRC"),
        "__TIP_ERAFIP__": (FORMULAS["ERA"] + " ／ " + FORMULAS["FIP"]).replace('"', "&quot;"),
    }
    for k, v in reps.items():
        html = html.replace(k, v)
    return html


_TEMPLATE = _inject_headers(_TEMPLATE)
