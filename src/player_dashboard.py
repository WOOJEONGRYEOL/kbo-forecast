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
    "구장차": "순수 wRC+ − 이벤트 wRC+. 클수록 큰 구장에서 장타를 손해 보는 타자.",
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


def save_player_dashboard(pitchers, batters, p_screens, b_screens, lg_era,
                          latest_game=None):
    """선수 평가 대시보드를 data/players.html 로 저장합니다.

    latest_game : 반영된 최신 경기일(투수 박스스코어 기준). 자막에 표시.
    """

    def pit_rec(r):
        return {
            "name": r["name"], "team": r["team"],
            "teamName": config.TEAM_NAMES.get(r["team"], r["team"]),
            "color": TEAM_COLORS.get(r["team"], "#888"),
            "stuff": _round(r["k_stuff_v2"], 1), "control": _round(r["k_control_v2"], 1),
            "era": _round(r["era"], 2), "fip": _round(r["fip"], 2),
            "gap": _round(r["era_fip_gap"], 2), "ip": _round(r["ip"], 1),
            "type": r["type"],
            "whiff": _round((r.get("whiff_rate") or 0) * 100, 1),
            "csw": _round((r.get("csw_rate") or 0) * 100, 1),
            "speed": _round(r.get("avg_speed"), 1),
            "arsenal": _arsenal(r.get("pitch_type_details")),
        }

    def bat_rec(r):
        return {
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

    data = {
        "generated": str(date.today()), "season": config.SEASON,
        "lgEra": round(lg_era, 2), "stuffHigh": 105, "stuffLow": 97,
        "lgBabip": _round(batters.attrs.get("lg_babip"), 3),
        "logos": logo_map(),
        "pitchers": [pit_rec(r) for _, r in pitchers.iterrows()],
        "batters": [bat_rec(r) for _, r in batters.iterrows()],
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
        ("PARK", b_screens["park_victim"], "park_gap", "tb_park"),
    ]:
        html = html.replace(f"__T_{key}__", _batter_rows(scr, col))
        html = html.replace(f"__M_{key}__", _more(len(scr), tb))

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
  .tip:hover::after, .tip:active::after { content: attr(data-tip);
    position: absolute; left: 50%; transform: translateX(-50%); bottom: 150%;
    width: min(260px, 78vw); white-space: normal; text-align: left; background: #0b0e14;
    color: var(--text); border: 1px solid var(--line); padding: 9px 11px;
    border-radius: 8px; font-size: 12px; font-weight: 400; line-height: 1.55;
    z-index: 20; box-shadow: 0 8px 24px rgba(0,0,0,.55); }

  /* 랜덤 클릭으로 뽑힌 선수 상세 카드 */
  .pick { margin-top: 12px; padding: 12px 13px; background: #0b0e14;
    border: 1px solid var(--line); border-radius: 9px; }
  .pick-info { display: flex; align-items: center; gap: 11px; }
  .pick-info img { width: 34px; height: 34px; object-fit: contain; flex: none; }
  .pick-info .nm { font-weight: 700; font-size: 14px; }
  .pick-info .meta { color: var(--muted); font-size: 12px; line-height: 1.5; }
  .radar-wrap { position: relative; height: 200px; margin-top: 6px; }
  /* 투수 아스널 막대 */
  .arsenal { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
  .ars-row { display: flex; align-items: center; gap: 9px; font-size: 11.5px; }
  .ars-name { width: 82px; color: var(--text); flex: none; }
  .ars-bar { flex: 1; height: 15px; background: #131a26; border-radius: 4px; overflow: hidden; }
  .ars-fill { height: 100%; background: var(--blue); }
  .ars-num { width: 150px; text-align: right; color: var(--muted); flex: none; }

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
</div>
<h1>⚾ KBO __SEASON__ 선수 평가 대시보드</h1>
<div class="sub"><span class="stamp">🕗 최종 갱신 __STAMP__ · <b>__LATEST__ 경기까지 반영</b> · 매일 오전 8시(KST) 자동 갱신</span><br>
  투수: 구위×성적 사분면 + 구종 아스널 · 타자: BABIP운 + 5툴 레이더 + FCB 승리기여 · 리그 평균 ERA __LG_ERA__ ·
  <b>지표 이름 호버=공식, 그래프 클릭=랜덤 선수 상세</b></div>

<div class="grid">

  <div class="card">
    <h2>투수 사분면 — 구위 vs 성적</h2>
    <p class="hint">→구위 좋음 ↑ERA 나쁨. <b>오른쪽 위 = 억울한 투수📈</b>,
      왼쪽 아래 = 시한폭탄⚠️. 점 크기 = 이닝 · <b>클릭 = 랜덤 투수 + 구종 아스널</b></p>
    <div class="chart-box"><canvas id="quadChart"></canvas></div>
    <div class="pick">
      <div class="pick-info" id="pick_quad_info"></div>
      <div class="arsenal" id="arsenal_quad"></div>
    </div>
  </div>

  <div class="card">
    <h2>수비/운 피해 순위 — <span class="tip" data-tip="__TIP_ERAFIP__">ERA − FIP</span></h2>
    <p class="hint">FIP(수비 무관)보다 ERA가 높을수록 수비·운의 피해자.
      막대가 길수록 '실제 실력보다 성적이 억울한' 투수</p>
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
    <p class="hint">순수 wRC+(비거리 기반) − 이벤트 wRC+(실제 결과) 격차.
      큰 구장 담장 앞에서 홈런을 도둑맞고 있는 타자들</p>
    <table><thead><tr><th>__H_BAT__</th><th>팀</th><th>__H_PA__</th><th>__H_OVR__</th><th>__H_WRC__</th><th>__H_PARK__</th></tr></thead>
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
        <h3>ERA − FIP 격차 · 구장차</h3>
        <div class="eq">수비·운 피해 = ERA − FIP
구장차 = wRC+<sub>pure</sub> − wRC+<sub>event</sub></div>
        <div class="note">ERA−FIP가 클수록 실력 대비 성적이 억울한 투수.
          구장차가 클수록 큰 구장(잠실·대구) 담장 앞에서 장타를 손해 보는 타자.</div>
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
const DATA = __DATA__;
Chart.defaults.color = "#8a94a8";
Chart.defaults.borderColor = "#2a3345";
Chart.defaults.font.family = '"Apple SD Gothic Neo","Noto Sans KR",sans-serif';

const rIp = ip => Math.max(3, Math.min(11, ip / 12));
const rPa = pa => Math.max(3, Math.min(11, pa / 45));

// 선택된 선수를 그래프 위에서 흰 링으로 하이라이트하는 플러그인.
// chart._sel(선택 인덱스)를 매 프레임 읽어 그 점 둘레에 원을 그립니다.
const hlPlugin = {
  id: "highlight",
  afterDatasetsDraw(chart) {
    const i = chart._sel;
    if (i == null) return;
    const pt = chart.getDatasetMeta(0).data[i];
    if (!pt) return;
    const r = (chart.data.datasets[0].data[i].r || 6) + 7;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath(); ctx.arc(pt.x, pt.y, r, 0, 2 * Math.PI);
    ctx.lineWidth = 3; ctx.strokeStyle = "#ffffff"; ctx.stroke();
    ctx.beginPath(); ctx.arc(pt.x, pt.y, r + 3, 0, 2 * Math.PI);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = (chart.data.datasets[0].data[i].color || "#fff") + "aa";
    ctx.stroke();
    ctx.restore();
  }
};

// 그래프 클릭 → 랜덤 선수(점 클릭 시 그 선수). 5초마다 자동 순환.
// 선택된 선수는 상세 카드 + 그래프 하이라이트로 표시.
function attachRandomPick(chart, infoId, fmt, onPick) {
  const info = document.getElementById(infoId);
  const arr = chart.data.datasets[0].data;
  let timer = null;
  function show(i) {
    chart._sel = i;
    const d = arr[i];
    info.innerHTML = fmt(d);
    if (onPick) onPick(d);
    chart.update("none");           // 애니메이션 없이 하이라이트만 갱신
  }
  function rnd() { show(Math.floor(Math.random() * arr.length)); }
  function restart() { clearInterval(timer); timer = setInterval(rnd, 5000); }
  chart.options.onClick = (e, els) => {
    show((els && els.length) ? els[0].index : Math.floor(Math.random() * arr.length));
    restart();                      // 수동 클릭하면 타이머 리셋
  };
  rnd();       // 처음에 한 명
  restart();   // 5초 자동 순환 시작
}
function infoHtml(d, lines) {
  return `<img src="${DATA.logos[d.team]}" alt="">
    <div><div class="nm">${d.name} <span class="meta">${d.teamName}</span></div>
    <div class="meta">${lines}</div></div>`;
}

// 5툴 레이더 (선구/컨택/타격/파워/주루) — 카드마다 1개, 픽 때 데이터만 교체
function makeRadar(canvasId) {
  return new Chart(document.getElementById(canvasId), {
    type: "radar",
    data: { labels: ["선구", "컨택", "타격", "파워", "주루"],
      datasets: [{ data: [100,100,100,100,100], borderWidth: 2,
        pointRadius: 3, fill: true }] },
    options: { maintainAspectRatio: false,
      scales: { r: { min: 50, max: 150, ticks: { stepSize: 25, backdropColor: "transparent", color: "#5b647a" },
        grid: { color: "#2a3345" }, angleLines: { color: "#2a3345" },
        pointLabels: { color: "#b8c0d0", font: { size: 12 } } } },
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: c => c.label + " " + c.raw } } } }
  });
}
function updateRadar(radar, d) {
  radar.data.datasets[0].data = [d.eye, d.vision, d.hit, d.power, d.baseR];
  radar.data.datasets[0].borderColor = d.color;
  radar.data.datasets[0].backgroundColor = d.color + "33";
  radar.data.datasets[0].pointBackgroundColor = d.color;
  radar.update();
}
// 투수 아스널 막대 렌더
function renderArsenal(elId, d) {
  const el = document.getElementById(elId);
  if (!d.arsenal || !d.arsenal.length) { el.innerHTML = ""; return; }
  const max = Math.max(...d.arsenal.map(a => a.usage));
  el.innerHTML = d.arsenal.map(a => `
    <div class="ars-row">
      <span class="ars-name">${a.group}</span>
      <span class="ars-bar"><span class="ars-fill" style="width:${(a.usage/max*100).toFixed(0)}%"></span></span>
      <span class="ars-num">구사 ${a.usage}% · 구위 ${a.stuff} · 헛스윙 ${a.whiff}%</span>
    </div>`).join("");
}

// ── ① 투수 사분면 ──
const quad = new Chart(document.getElementById("quadChart"), {
  type: "scatter",
  data: { datasets: [{
    data: DATA.pitchers.map(p => ({x: p.stuff, y: p.era, r: rIp(p.ip), ...p})),
    pointBackgroundColor: DATA.pitchers.map(p => p.color + "cc"),
    pointRadius: c => c.raw.r, pointHoverRadius: c => c.raw.r + 3 }]},
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
      `${c.raw.name}(${c.raw.team}) 구위+ ${c.raw.stuff} / ERA ${c.raw.era} / FIP ${c.raw.fip} — ${c.raw.type}` }}},
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
attachRandomPick(quad, "pick_quad_info",
  d => infoHtml(d, `이닝 ${d.ip} · ERA ${d.era} · FIP ${d.fip} · 구위+ ${d.stuff} · 제구+ ${d.control} · 평균구속 ${d.speed} · ${d.type}`),
  d => renderArsenal("arsenal_quad", d));

// ── ② ERA-FIP 격차 막대 ──
const victims = [...DATA.pitchers].sort((a,b) => b.gap - a.gap).slice(0, 12);
new Chart(document.getElementById("gapChart"), {
  type: "bar",
  data: { labels: victims.map(p => `${p.name}(${p.team})`),
    datasets: [{ data: victims.map(p => p.gap),
      backgroundColor: victims.map(p => p.gap > 0 ? "#ffb454" : "#3ecf8e"), borderRadius: 4 }]},
  options: { indexAxis: "y", maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => {
      const p = victims[c.dataIndex]; return `ERA ${p.era} − FIP ${p.fip} = ${p.gap > 0 ? "+" : ""}${p.gap}`; }}}},
    scales: { x: { grid: { color: "#222a3a" }, title: { display: true, text: "ERA − FIP" } }, y: { grid: { display: false } } } }
});

// ── ③ 타자 운 산점도 (BABIP vs wOBA) ──
const bats = DATA.batters.filter(b => b.woba != null && b.babip != null);
const batLuck = new Chart(document.getElementById("batLuckChart"), {
  type: "scatter",
  data: { datasets: [{
    data: bats.map(b => ({x: b.babip, y: b.woba, r: rPa(b.pa), ...b})),
    pointBackgroundColor: bats.map(b => b.color + "cc"),
    pointRadius: c => c.raw.r, pointHoverRadius: c => c.raw.r + 3 }]},
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
      `${c.raw.name}(${c.raw.team}) BABIP ${c.raw.x.toFixed(3)} / wOBA ${c.raw.y.toFixed(3)} / ${c.raw.pa}타석` }}},
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
attachRandomPick(batLuck, "pick_luck_info",
  d => infoHtml(d, `타석 ${d.pa} · BABIP ${d.babip} · wOBA ${d.woba} · 종합+ ${d.overall} · 유인구스윙 ${d.chase}% · 컨택 ${d.contact}% · ${d.luckType}`),
  d => updateRadar(radarLuck, d));

// ── ④ 파워 유형 지도 ──
const pw = DATA.batters.filter(b => b.power != null && b.hr != null);
const power = new Chart(document.getElementById("powerChart"), {
  type: "scatter",
  data: { datasets: [{
    data: pw.map(b => ({x: b.power, y: b.hr, r: rPa(b.pa), ...b})),
    pointBackgroundColor: pw.map(b => b.color + "cc"),
    pointRadius: c => c.raw.r, pointHoverRadius: c => c.raw.r + 3 }]},
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c =>
      `${c.raw.name}(${c.raw.team}) Power+ ${c.raw.x} / HR+ ${c.raw.y}` }}},
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
attachRandomPick(power, "pick_power_info",
  d => infoHtml(d, `타석 ${d.pa} · Power+ ${d.power} · HR+ ${d.hr} · ISO ${d.iso} · ${d.powerType}`),
  d => updateRadar(radarPower, d));

// ── '더 보기' 토글 ──
document.querySelectorAll(".more").forEach(btn => {
  const tb = document.getElementById(btn.dataset.tb);
  const hidden = tb.querySelectorAll("tr.row-hidden");
  btn.addEventListener("click", () => {
    const opening = hidden[0].style.display !== "table-row";
    hidden.forEach(tr => { tr.style.display = opening ? "table-row" : "none"; });
    btn.textContent = opening ? "− 접기" : `＋ 더 보기 (${hidden.length}명 더)`;
  });
});
</script>
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
        "__H_LUCK__": _tip("운", "운"), "__H_PARK__": _tip("구장차", "구장차"),
        "__H_WINS__": _tip("승리기여", "승리기여"), "__H_SRC__": _tip("경기당SRC", "경기당SRC"),
        "__TIP_ERAFIP__": (FORMULAS["ERA"] + " ／ " + FORMULAS["FIP"]).replace('"', "&quot;"),
    }
    for k, v in reps.items():
        html = html.replace(k, v)
    return html


_TEMPLATE = _inject_headers(_TEMPLATE)
