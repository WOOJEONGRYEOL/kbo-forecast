# -*- coding: utf-8 -*-
"""
dashboard.py — HTML 대시보드 생성기 (팀 단기 전력)
====================================================

[핵심 설계] 피타고리안·괴리율·모멘텀 계산을 브라우저(JS)에서 즉석으로 합니다.
  파이썬은 '원시 경기 로그 + 고정 지표(구위/타선/클러치)'만 넘기고,
  사용자가 슬라이더로 고른 '최근 N경기'(또는 시즌 전체)에 맞춰
  표·모멘텀 막대·운 산점도·시즌 흐름을 실시간 재계산합니다.
  → 10·20 같은 고정 단위가 아니라 임의의 경기 수를 자유롭게 선택 가능.

[구성]
  ① 진단 테이블   — 현재 순위 순. 윈도우/투수력 토글에 따라 셀 실시간 갱신
  ② 모멘텀 바      — 선택 윈도우 기준
  ③ 운 산점도     — 기대 vs 실제 (점=팀 로고)
  ④ 시즌 흐름     — 직전 N경기(또는 누적) 기대승률 추이 (범례=로고)
  ⑤ 선발 로테이션 — 토글 '선발 로테이션'의 근거
"""

import base64
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

import config

# 한국 표준시 (GitHub Actions는 UTC로 도므로 갱신 시각을 KST로 환산해 표시)
KST = timezone(timedelta(hours=9))


def _gen_stamp() -> str:
    """대시보드 생성(갱신) 시각을 'YYYY-MM-DD HH:MM KST'로."""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

# 시즌 흐름 라인 차트는 10개 선을 색으로 구분해야 해서 팀 상징색 유지
TEAM_COLORS = {
    "LG": "#C30452", "OB": "#2E2A66", "HT": "#EA0029", "SS": "#0766C2",
    "SK": "#CE0E2D", "LT": "#00275E", "HH": "#FF6600", "WO": "#820024",
    "NC": "#315288", "KT": "#666666",
}

LOGO_BASE = "https://sports-phinf.pstatic.net/team/kbo/default/"
LOGO_DIR = Path(config.DATA_DIR) / "logos"


def logo_url(code: str) -> str:
    return f"{LOGO_BASE}{code}.png"


def _ensure_logo(code: str) -> Path:
    """로고 PNG를 data/logos/에 1회 내려받아 캐시하고 경로를 반환합니다."""
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGO_DIR / f"{code}.png"
    if not path.exists():
        resp = requests.get(logo_url(code),
                            headers={"User-Agent": config.USER_AGENT}, timeout=15)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return path


def logo_data_uri(code: str) -> str:
    """
    로고를 base64 data URI로 반환합니다.
    네이버 CDN이 Referer로 핫링크를 차단하므로 원격 <img src>는 깨집니다.
    파일에 심어 넣으면 어디로 옮겨도(파일 더블클릭 포함) 항상 표시됩니다.
    """
    b64 = base64.b64encode(_ensure_logo(code).read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def logo_map() -> dict:
    return {code: logo_data_uri(code) for code in config.TEAM_NAMES}


# ── 지표 산정 공식 (헤더 호버 툴팁) ──
FORMULAS = {
    "순위": "분석 시점의 시즌 승률 순위 (무승부 제외: 승÷(승+패)).",
    "최근성적": "선택한 윈도우(최근 N경기 또는 시즌 전체)의 승-패-무.",
    "실제승률": "(승 + 0.5×무) ÷ 경기수. 무승부는 0.5승으로 계산해 기대승률과 척도를 맞춥니다.",
    "기대승률": "피타고리안 기대승률 = 득점^1.83 ÷ (득점^1.83 + 실점^1.83). "
                "'득실점 마진이 곧 실력'이라는 세이버메트릭스 대원칙.",
    "괴리율": "기대승률 − 실제승률. + 이면 경기력 대비 운이 없었던 팀(반등 후보), "
              "− 이면 운이 좋았던 팀(하락 경계).",
    "구위+": "팀 투수진 K-Stuff+ (투구수 가중평균, 100=리그평균). 토글로 '선발 로테이션'만으로 전환 가능.",
    "타선+": "팀 타선 순수 wRC+ (타석수 가중평균, 파크팩터·비거리 보정, 100=리그평균).",
    "클러치": "FCB 팀 승리기여 합. ⚠️ 클러치는 잘 지속되지 않아 미래 예측이 아닌 "
              "'지금까지 승부처에 강했나'를 보여주는 설명형 지표(모멘텀 미반영).",
    "모멘텀": "0.5×z(괴리율) + 0.25×z(구위+) + 0.25×z(타선+). z=표준점수. "
              "윈도우를 바꾸면 괴리율이 바뀌어 모멘텀도 실시간 재계산됩니다.",
    "진단": "괴리율이 ±0.05를 넘으면 반등/하락으로 판정.",
}


def _tip(label: str, key: str) -> str:
    tip = FORMULAS.get(key, "").replace('"', "&quot;")
    return f'<span class="tip" data-tip="{tip}">{label}</span>'


def _table_rows(standings: pd.DataFrame, logos: dict) -> str:
    """
    표 골격만 만듭니다 (현재 순위·로고·팀명·타선+·클러치는 고정).
    윈도우에 따라 바뀌는 셀(최근성적/실제/기대/괴리/구위+/모멘텀/진단)은
    비워두고 JS가 실시간으로 채웁니다.
    """
    rows = []
    for rank, (team, r) in enumerate(standings.iterrows(), start=1):
        name = config.TEAM_NAMES.get(team, team)
        fcb = "-" if pd.isna(r["team_fcb"]) else int(round(r["team_fcb"]))
        rows.append(f"""
        <tr data-team="{team}">
          <td class="rank">{rank}</td>
          <td class="team"><img class="logo" src="{logos[team]}" alt="">{name}</td>
          <td class="c-rec"></td>
          <td class="c-act"></td>
          <td class="c-exp"></td>
          <td class="c-gap"></td>
          <td class="c-stuff"></td>
          <td>{r['bat_wrc_pure']:.1f}</td>
          <td>{fcb}</td>
          <td class="c-mom"></td>
          <td class="c-diag diag"></td>
        </tr>""")
    return "".join(rows)


def _rotation_rows(standings, logos, rotation_detail: dict) -> str:
    """선발 로테이션 카드: 팀별 선발진(이름·선발수·구위·주무기)."""
    if not rotation_detail:
        return ""
    cards = []
    for team, r in standings.iterrows():
        name = config.TEAM_NAMES.get(team, team)
        starters = rotation_detail.get(team, [])
        chips = "".join(
            f'<div class="sp">{s["name"]} <span class="spx">{s["starts"]}선발 · '
            f'구위 {s["stuff"]} · {s["top"]}</span></div>'
            for s in starters
        ) or '<div class="spx">식별된 선발 없음</div>'
        cards.append(
            f'<div class="rot-team"><div class="rot-head">'
            f'<img class="logo" src="{logos[team]}" alt="">{name} '
            f'<span class="spx">선발 K-Stuff+ {r["team_stuff_rot"]:.1f}</span></div>'
            f'{chips}</div>')
    return "".join(cards)


def save_dashboard(df: pd.DataFrame, team_log: pd.DataFrame, window: int,
                   rotation_detail: dict | None = None) -> Path:
    """
    대시보드 HTML을 data/dashboard.html 로 저장하고 경로를 돌려줍니다.

    df         : model.combine() 결과 (고정 지표 + 현재 순위)
    team_log   : 원시 팀 경기 로그 (JS 즉석 계산의 재료)
    window     : 슬라이더 초기값
    """
    standings = df.sort_values("season_wpct", ascending=False)
    logos = logo_map()
    order = list(standings.index)

    # 팀별 고정 지표 (윈도우와 무관)
    meta = {
        team: {
            "name": config.TEAM_NAMES.get(team, team),
            "color": TEAM_COLORS.get(team, "#888"),
            "stuffAll": round(float(r["team_stuff_plus"]), 1),
            "stuffRot": round(float(r["team_stuff_rot"]), 1),
            "batWrc": round(float(r["bat_wrc_pure"]), 1),
        }
        for team, r in standings.iterrows()
    }

    # 팀별 원시 경기 로그 (날짜순): d=날짜, rf=득점, ra=실점, r=결과
    games = {}
    max_games = 0
    for team, g in team_log.groupby("team"):
        g = g.sort_values("date")
        games[team] = [
            {"d": row["date"], "rf": int(row["runs_for"]),
             "ra": int(row["runs_against"]), "r": row["result"]}
            for _, row in g.iterrows()
        ]
        max_games = max(max_games, len(games[team]))

    payload = {
        "logos": logos, "order": order, "meta": meta, "games": games,
        "maxGames": max_games, "defaultWindow": window,
        "exp": config.PYTHAG_EXPONENT, "gapTh": config.GAP_THRESHOLD,
        "season": config.SEASON, "today": str(date.today()),
    }

    # 반영된 최신 경기일 (팀 로그의 마지막 날짜)
    latest_game = max((g[-1]["d"] for g in games.values() if g), default="-")

    html = _TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    html = html.replace("__TABLE_ROWS__", _table_rows(standings, logos))
    html = html.replace("__ROTATION_ROWS__",
                        _rotation_rows(standings, logos, rotation_detail or {}))
    html = html.replace("__SEASON__", str(config.SEASON))
    html = html.replace("__STAMP__", _gen_stamp())
    html = html.replace("__LATEST__", latest_game)
    html = _inject_headers(html)

    out = Path(config.DATA_DIR) / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


def _inject_headers(html: str) -> str:
    return (html
            .replace("__H_RANK__", _tip("#", "순위"))
            .replace("__H_RECENT__", _tip("최근성적", "최근성적"))
            .replace("__H_ACTUAL__", _tip("실제승률", "실제승률"))
            .replace("__H_EXPECTED__", _tip("기대승률", "기대승률"))
            .replace("__H_GAP__", _tip("괴리율", "괴리율"))
            .replace("__H_STUFF__", _tip("구위+", "구위+"))
            .replace("__H_BAT__", _tip("타선+", "타선+"))
            .replace("__H_CLUTCH__", _tip("클러치", "클러치"))
            .replace("__H_MOM__", _tip("모멘텀", "모멘텀"))
            .replace("__H_DIAG__", _tip("진단", "진단")))


# ──────────────────────────────────────────────────────────────
# HTML 템플릿
# ──────────────────────────────────────────────────────────────

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KBO __SEASON__ 단기 전력 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root { --bg: #10141c; --card: #1a212e; --line: #2a3345;
    --text: #e8ecf3; --muted: #8a94a8; --green: #3ecf8e; --red: #ff6b6b; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; line-height: 1.7; }
  .sub .stamp { color: var(--text); }
  .sub .stamp b { color: var(--green); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .card { background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 18px; min-width: 0; }  /* min-width:0 → 표 가로스크롤이 카드 안에서 작동 */
  .card.wide { grid-column: 1 / -1; }
  .table-scroll { max-width: 100%; }
  .card h2 { font-size: 15px; margin: 0 0 4px; }
  .card .hint { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
  @media (max-width: 980px) { .grid { grid-template-columns: 1fr; } }
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 7px 8px; text-align: right; white-space: nowrap; }
  th { color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); }
  td { border-bottom: 1px solid #222a3a; }
  th:nth-child(2), td:nth-child(2) { text-align: left; }
  td.diag { text-align: left; font-size: 12px; }
  td.rank { color: var(--muted); }
  td.team { font-weight: 600; }
  .pos { color: var(--green); font-weight: 700; }
  .neg { color: var(--red); font-weight: 700; }
  .logo { width: 20px; height: 20px; object-fit: contain; margin-right: 8px; vertical-align: -5px; }
  .chart-box { position: relative; height: 320px; }
  .chart-box.tall { height: 380px; }

  /* ── 윈도우/투수력 컨트롤 바 ── */
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 16px 20px;
    padding: 12px 14px; margin-bottom: 14px; background: #131a26;
    border: 1px solid var(--line); border-radius: 10px; font-size: 12.5px; color: var(--muted); }
  .ctl { display: flex; align-items: center; gap: 9px; }
  .ctl b { color: var(--text); }
  #winValue { color: var(--green); font-weight: 700; min-width: 92px; }
  input[type=range] { width: 220px; accent-color: var(--green); cursor: pointer; }
  input[type=range]:disabled { opacity: .4; cursor: not-allowed; }
  .switch { position: relative; display: inline-block; width: 40px; height: 22px; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch .slider { position: absolute; inset: 0; cursor: pointer; border-radius: 999px;
    background: #2a3345; transition: .2s; }
  .switch .slider::before { content: ""; position: absolute; height: 16px; width: 16px;
    left: 3px; bottom: 3px; background: #e8ecf3; border-radius: 50%; transition: .2s; }
  .switch input:checked + .slider { background: #3ecf8e; }
  .switch input:checked + .slider::before { transform: translateX(18px); }
  .ctl .val { color: var(--text); font-weight: 600; min-width: 74px; }

  /* 선발 로테이션 카드 */
  .rotation-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
  @media (max-width: 980px) { .rotation-grid { grid-template-columns: repeat(2, 1fr); } }
  .rot-team { background: #131a26; border: 1px solid var(--line); border-radius: 9px; padding: 10px; }
  .rot-head { font-weight: 700; font-size: 12.5px; margin-bottom: 7px; }
  .rot-head .logo { width: 17px; height: 17px; vertical-align: -4px; margin-right: 5px; }
  .rot-team .sp { font-size: 12px; padding: 3px 0; border-top: 1px solid #222a3a; }
  .rot-team .spx { color: var(--muted); font-size: 11px; font-weight: 400; }

  /* 로고 범례 */
  .logo-legend { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; margin-top: 14px; }
  .logo-legend .lg-item { display: flex; align-items: center; gap: 5px; padding: 4px 9px;
    border: 1px solid var(--line); border-radius: 999px; cursor: pointer; font-size: 12px; user-select: none; }
  .logo-legend .lg-item img { width: 17px; height: 17px; object-fit: contain; }
  .logo-legend .lg-item.off { opacity: .35; }

  /* 산식 카드 */
  .formula-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
  @media (max-width: 980px) { .formula-grid { grid-template-columns: 1fr; } }
  .fblock { background: #131a26; border: 1px solid var(--line); border-radius: 9px; padding: 12px 14px; }
  .fblock h3 { font-size: 13px; margin: 0 0 8px; color: var(--text); }
  .fblock .eq { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
    color: #cfe3ff; background: #0b0e14; border: 1px solid var(--line); border-radius: 6px;
    padding: 8px 10px; line-height: 1.8; overflow-x: auto; white-space: pre-line; }
  .fblock .note { color: var(--muted); font-size: 11.5px; margin-top: 7px; line-height: 1.55; }

  /* 공식 툴팁 (:active로 모바일 탭도 지원, 폭은 화면 넘지 않게 제한) */
  .tip { position: relative; cursor: help; border-bottom: 1px dotted var(--muted); }
  .tip:hover::after, .tip:active::after { content: attr(data-tip); position: absolute; left: 50%;
    transform: translateX(-50%); bottom: 150%; width: min(250px, 78vw); white-space: normal;
    text-align: left; background: #0b0e14; color: var(--text); border: 1px solid var(--line);
    padding: 9px 11px; border-radius: 8px; font-size: 12px; font-weight: 400;
    line-height: 1.55; z-index: 20; box-shadow: 0 8px 24px rgba(0,0,0,.55); }

  /* ── 모바일(좁은 폰) 최적화 ── */
  @media (max-width: 560px) {
    body { padding: 12px; }
    h1 { font-size: 18px; }
    .sub { font-size: 12px; }
    .card { padding: 13px; }
    .card h2 { font-size: 14px; }
    .controls { gap: 10px 14px; padding: 10px 12px; }
    input[type=range] { width: 150px; }
    table { font-size: 11px; }
    th, td { padding: 5px 5px; }
    .logo { width: 17px; height: 17px; margin-right: 5px; }
    .chart-box { height: 300px; }
    .chart-box.tall { height: 340px; }
    .rotation-grid { grid-template-columns: 1fr; }
    .formula-grid { gap: 10px; }
    .fblock .eq { font-size: 12px; }
  }
</style>
</head>
<body>

<h1>⚾ KBO __SEASON__ 단기 전력 대시보드</h1>
<div class="sub"><span class="stamp">🕗 최종 갱신 __STAMP__ · <b>__LATEST__ 경기까지 반영</b> · 매일 오전 8시(KST) 자동 갱신</span><br>
  데이터: 네이버 스포츠(경기결과) + KBO Talent(세이버 지표) ·
  아래 슬라이더로 기준 경기 수를 자유롭게 바꾸면 표·차트가 실시간 재계산됩니다</div>

<div class="controls">
  <div class="ctl">
    <b>분석 기준</b>
    <input type="range" id="winSlider" min="5" max="10" value="10" step="1">
    <span id="winValue">최근 10경기</span>
  </div>
  <div class="ctl">
    <span>시즌 전체</span>
    <label class="switch"><input type="checkbox" id="seasonToggle"><span class="slider"></span></label>
  </div>
  <div class="ctl">
    <span>투수력 기준</span>
    <label class="switch"><input type="checkbox" id="rotToggle"><span class="slider"></span></label>
    <span class="val" id="rotLabel">전체 투수진</span>
    <span class="tip" data-tip="켜면 구위+와 모멘텀을 '선발 로테이션(선발로 자주 나온 투수들)'의 K-Stuff+로 다시 계산합니다.">ⓘ</span>
  </div>
</div>

<div class="grid">

  <div class="card wide">
    <h2>팀별 진단표 <span style="color:var(--muted);font-weight:400">— 현재 순위 순</span></h2>
    <p class="hint">위에서부터 현재 시즌 순위. 괴리율 <span class="pos">+초록</span>=경기력 대비 불운(반등 후보),
      <span class="neg">−빨강</span>=과실현(하락 경계)</p>
    <div class="table-scroll">
    <table>
      <thead><tr>
        <th>__H_RANK__</th><th>팀</th><th>__H_RECENT__</th><th>__H_ACTUAL__</th><th>__H_EXPECTED__</th>
        <th>__H_GAP__</th><th>__H_STUFF__</th><th>__H_BAT__</th><th>__H_CLUTCH__</th><th>__H_MOM__</th><th>__H_DIAG__</th>
      </tr></thead>
      <tbody>__TABLE_ROWS__</tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h2>종합 모멘텀 지수</h2>
    <p class="hint">막대가 길수록 단기 미래가 밝음. 현재 순위 순 (위=1위)</p>
    <div class="chart-box"><canvas id="momentumChart"></canvas></div>
  </div>

  <div class="card">
    <h2>운 산점도 — 기대 vs 실제</h2>
    <p class="hint">대각선 위 = 성적이 경기력보다 좋았던 팀(운↑), 아래 = 억울한 팀(운↓)</p>
    <div class="chart-box"><canvas id="luckChart"></canvas></div>
  </div>

  <div class="card wide">
    <h2>시즌 흐름 — 기대승률 추이</h2>
    <p class="hint">선택 기준으로 계산한 기대승률의 시계열(시즌 전체는 누적). 아래 로고를 클릭하면 선을 켜고 끕니다.</p>
    <div class="chart-box tall"><canvas id="trendChart"></canvas></div>
    <div id="trendLegend" class="logo-legend"></div>
  </div>

  <div class="card wide">
    <h2>선발 로테이션 아스널 <span style="color:var(--muted);font-weight:400">— 위 '선발 로테이션' 토글의 근거</span></h2>
    <p class="hint">박스스코어에서 식별한 팀별 선발진(선발 등판 3회 이상). 각 선발의 K-Stuff+와 주무기 구종.</p>
    <div class="rotation-grid">__ROTATION_ROWS__</div>
  </div>

  <div class="card wide">
    <h2>📐 산식 &amp; 방법론</h2>
    <p class="hint">이 대시보드의 모든 숫자가 어떻게 계산되는지. z(x)=(x−리그평균)÷표준편차 (표준점수).</p>
    <div class="formula-grid">

      <div class="fblock">
        <h3>종합 모멘텀 지수</h3>
        <div class="eq">모멘텀 = 0.50·z(괴리율) + 0.25·z(구위+) + 0.25·z(타선+)</div>
        <div class="note">단기 예측의 지배 신호는 '운의 되돌림'이라 괴리율에 절반, 투타 실력에 각 1/4.
          '선발 로테이션' 토글을 켜면 구위+가 선발진 K-Stuff+로 교체됩니다.
          가중치는 출발점일 뿐 — 백테스트로 캘리브레이션 대상.</div>
      </div>

      <div class="fblock">
        <h3>피타고리안 기대승률</h3>
        <div class="eq">기대승률 = 득점<sup>1.83</sup> ÷ (득점<sup>1.83</sup> + 실점<sup>1.83</sup>)</div>
        <div class="note">선택한 최근 N경기(또는 시즌 전체)의 득·실점 합을 사용. 지수 1.83은
          빌 제임스 원조(2)를 실측 보정한 표준값. "득실점 마진이 곧 실력".</div>
      </div>

      <div class="fblock">
        <h3>실제승률 · 괴리율</h3>
        <div class="eq">실제승률 = (승 + 0.5×무) ÷ 경기수
괴리율 = 기대승률 − 실제승률</div>
        <div class="note">무승부는 기대승률과 척도를 맞추려 0.5승으로 계산.
          괴리율 + = 경기력 대비 불운(반등 후보), − = 과실현(하락 경계).</div>
      </div>

      <div class="fblock">
        <h3>팀 구위+ (K-Stuff+ 집계)</h3>
        <div class="eq">전체 투수진 = Σ(투수 K-Stuff+ × 투구수) ÷ Σ투구수
선발 로테이션 = Σ(선발 K-Stuff+ × 선발등판수) ÷ Σ선발등판수</div>
        <div class="note">많이 던진 투수일수록 팀 실점에 큰 영향 → 가중평균.
          토글이 두 식 사이를 전환합니다.</div>
      </div>

      <div class="fblock">
        <h3>팀 타선+ (순수 wRC+ 집계)</h3>
        <div class="eq">타선+ = Σ(타자 wRC+<sub>pure</sub> × 타석) ÷ Σ타석</div>
        <div class="note">wRC+<sub>pure</sub>는 파크팩터·타구 비거리까지 보정한 득점창출력.
          타석수로 가중해 팀 대표값 산출. 100=리그평균.</div>
      </div>

      <div class="fblock">
        <h3>클러치 (FCB)</h3>
        <div class="eq">클러치 = Σ(소속 타자 승리기여<sub>FCB</sub>)</div>
        <div class="note">협조적 게임이론(Shapley value)으로 '득점이 난 순간'의 기여를 공정 분배해 누적.
          ⚠️ 클러치는 잘 지속되지 않아 <b>모멘텀에는 반영하지 않는</b> 설명형 지표.</div>
      </div>

      <div class="fblock" style="grid-column: 1 / -1;">
        <h3>K-Stuff+ (구위) — 닫힌 산식이 없는 머신러닝 지표</h3>
        <div class="eq">K-Stuff+ = f(구속, 무브먼트[수평·수직], 회전, 릴리스포인트, 익스텐션 …)  → 100 스케일</div>
        <div class="note">메이저리그 Stuff+의 KBO 버전. 공의 <b>물리적 특성만</b>으로 기대 실점가치를 예측하는
          모델(원조는 XGBoost 계열)의 출력이라 대수식이 아닙니다. 안타·홈런 같은 '결과'와 공이 간
          '위치(제구)'는 배제 — 제구는 K-Location+/K-Control+가 따로 측정. 100=리그평균, 110↑ 상위권.
          구종별(직구·슬라이더·커브…)로 각각 산출돼 종합됩니다(선수 대시보드 아스널 참고).
          ※ 정확한 피처·모델은 kbostuff.app 내부 구현.</div>
      </div>

    </div>
  </div>

</div>

<script>
const DATA = __DATA__;
Chart.defaults.color = "#8a94a8";
Chart.defaults.borderColor = "#2a3345";
Chart.defaults.font.family = '"Apple SD Gothic Neo","Noto Sans KR",sans-serif';

const E = DATA.exp, TH = DATA.gapTh, order = DATA.order, meta = DATA.meta, games = DATA.games;

// ── z-score 유틸 ──
function zmap(obj) {
  const v = Object.values(obj);
  const m = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / v.length) || 1;
  const out = {}; for (const k in obj) out[k] = (obj[k] - m) / sd; return out;
}
// 고정 지표의 z는 한 번만 계산 (윈도우와 무관)
const zStuffAll = zmap(Object.fromEntries(order.map(c => [c, meta[c].stuffAll])));
const zStuffRot = zmap(Object.fromEntries(order.map(c => [c, meta[c].stuffRot])));
const zBat = zmap(Object.fromEntries(order.map(c => [c, meta[c].batWrc])));

// 최근 W경기(또는 시즌 전체=null) 요약
function summarize(gs, W) {
  const arr = (W == null) ? gs : gs.slice(-W);
  let rs = 0, ra = 0, w = 0, l = 0, d = 0;
  for (const g of arr) { rs += g.rf; ra += g.ra; if (g.r === "W") w++; else if (g.r === "L") l++; else d++; }
  const n = arr.length || 1;
  const exp = (rs === 0 && ra === 0) ? 0.5 : Math.pow(rs, E) / (Math.pow(rs, E) + Math.pow(ra, E));
  const act = (w + 0.5 * d) / n;
  return { w, l, d, actual: act, expected: exp, gap: exp - act };
}
function diagnose(gap) {
  if (gap > TH) return "📈 반등 후보 — 경기력 대비 운이 없었음";
  if (gap < -TH) return "📉 하락 경계 — 승리를 과하게 챙김";
  return "➖ 적정 — 성적이 경기력과 일치";
}

const rowEl = {}; order.forEach(c => rowEl[c] = document.querySelector(`tr[data-team="${c}"]`));

// 로고 캐시(산점도 point 이미지용)
const logoCache = {};
function teamLogo(code, size) {
  const k = code + "@" + size;
  if (!logoCache[k]) { const im = new Image(size, size); im.src = DATA.logos[code]; logoCache[k] = im; }
  return logoCache[k];
}

// ── 차트 생성 (빈 상태로 만들고 render가 채움) ──
const momentumChart = new Chart(document.getElementById("momentumChart"), {
  type: "bar",
  data: { labels: order.map(c => meta[c].name), datasets: [{ data: [], borderRadius: 4 }] },
  options: { indexAxis: "y", maintainAspectRatio: false,
    plugins: { legend: { display: false },
      tooltip: { callbacks: { label: c => `모멘텀 ${c.raw >= 0 ? "+" : ""}${(+c.raw).toFixed(2)}` } } },
    scales: { x: { grid: { color: "#222a3a" } }, y: { grid: { display: false } } } }
});

const luckChart = new Chart(document.getElementById("luckChart"), {
  type: "scatter",
  data: { datasets: [
    { data: [], pointStyle: order.map(c => teamLogo(c, 26)), pointRadius: 13, pointHoverRadius: 15 },
    { type: "line", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: "#3a4560",
      borderDash: [6, 6], borderWidth: 1, pointRadius: 0 }
  ] },
  options: { maintainAspectRatio: false,
    plugins: { legend: { display: false },
      tooltip: { callbacks: { label: c => c.raw.name
        ? `${c.raw.name}: 기대 ${c.raw.x.toFixed(3)} / 실제 ${c.raw.y.toFixed(3)}` : "" } } },
    scales: { x: { min: 0.1, max: 0.9, title: { display: true, text: "기대승률 (피타고리안)" }, grid: { color: "#222a3a" } },
      y: { min: 0.1, max: 0.9, title: { display: true, text: "실제승률" }, grid: { color: "#222a3a" } } } }
});

const trendChart = new Chart(document.getElementById("trendChart"), {
  type: "line",
  data: { labels: [], datasets: order.map(c => ({
    code: c, label: meta[c].name, data: [], borderColor: meta[c].color,
    backgroundColor: meta[c].color, borderWidth: 2, pointRadius: 0, spanGaps: true, tension: 0.25 })) },
  options: { maintainAspectRatio: false, interaction: { mode: "nearest", intersect: false },
    plugins: { legend: { display: false } },
    scales: { x: { ticks: { maxTicksLimit: 12 }, grid: { display: false } },
      y: { min: 0, max: 1, title: { display: true, text: "기대승률" }, grid: { color: "#222a3a" } } } }
});

// 시즌 흐름 시계열 (윈도우 W, null=누적)
function trendSeries(gs, W) {
  const dates = [], exp = [];
  for (let i = 0; i < gs.length; i++) {
    let arr;
    if (W == null) { if (i < 4) continue; arr = gs.slice(0, i + 1); }   // 누적(min 5)
    else { if (i < W - 1) continue; arr = gs.slice(i - W + 1, i + 1); }
    let rs = 0, ra = 0; for (const g of arr) { rs += g.rf; ra += g.ra; }
    dates.push(gs[i].d);
    exp.push((rs === 0 && ra === 0) ? 0.5 : Math.pow(rs, E) / (Math.pow(rs, E) + Math.pow(ra, E)));
  }
  return { dates, exp };
}

// ── 핵심: 선택 상태(W, rot)로 표·차트 전체 재계산 ──
function render() {
  const rot = document.getElementById("rotToggle").checked;
  const season = document.getElementById("seasonToggle").checked;
  const W = season ? null : parseInt(document.getElementById("winSlider").value, 10);
  const zStuff = rot ? zStuffRot : zStuffAll;

  const calc = {}, gapMap = {};
  order.forEach(c => { const s = summarize(games[c], W); calc[c] = s; gapMap[c] = s.gap; });
  const zGap = zmap(gapMap);

  order.forEach(c => {
    const s = calc[c], tr = rowEl[c];
    let rec = `${s.w}승 ${s.l}패`; if (s.d) rec += ` ${s.d}무`;
    tr.querySelector(".c-rec").textContent = rec;
    tr.querySelector(".c-act").textContent = s.actual.toFixed(3);
    tr.querySelector(".c-exp").textContent = s.expected.toFixed(3);
    const gapC = tr.querySelector(".c-gap");
    gapC.textContent = (s.gap >= 0 ? "+" : "") + s.gap.toFixed(3);
    gapC.className = "c-gap " + (s.gap > TH ? "pos" : (s.gap < -TH ? "neg" : ""));
    tr.querySelector(".c-stuff").textContent = (rot ? meta[c].stuffRot : meta[c].stuffAll).toFixed(1);
    const mom = 0.5 * zGap[c] + 0.25 * zStuff[c] + 0.25 * zBat[c];
    calc[c].mom = mom;
    const momC = tr.querySelector(".c-mom");
    momC.textContent = (mom >= 0 ? "+" : "") + mom.toFixed(2);
    momC.className = "c-mom " + (mom > 0 ? "pos" : "neg");
    tr.querySelector(".c-diag").textContent = diagnose(s.gap);
  });

  // 모멘텀 막대
  const momVals = order.map(c => calc[c].mom);
  momentumChart.data.datasets[0].data = momVals;
  momentumChart.data.datasets[0].backgroundColor = momVals.map(v => v >= 0 ? "#3ecf8e" : "#ff6b6b");
  momentumChart.update();

  // 운 산점도
  luckChart.data.datasets[0].data = order.map(c => ({ x: calc[c].expected, y: calc[c].actual, name: meta[c].name }));
  luckChart.update();

  // 시즌 흐름
  const series = {}; order.forEach(c => series[c] = trendSeries(games[c], W));
  const allDates = [...new Set(order.flatMap(c => series[c].dates))].sort();
  trendChart.data.labels = allDates;
  trendChart.data.datasets.forEach(ds => {
    const s = series[ds.code], byd = {};
    s.dates.forEach((d, i) => byd[d] = s.exp[i]);
    ds.data = allDates.map(d => d in byd ? +byd[d].toFixed(3) : null);
  });
  trendChart.update();
}

// ── 컨트롤 이벤트 ──
const slider = document.getElementById("winSlider");
const seasonToggle = document.getElementById("seasonToggle");
const rotToggle = document.getElementById("rotToggle");
slider.max = DATA.maxGames;
slider.value = Math.min(DATA.defaultWindow, DATA.maxGames);

function syncLabels() {
  const season = seasonToggle.checked;
  slider.disabled = season;
  document.getElementById("winValue").textContent = season ? "시즌 전체" : `최근 ${slider.value}경기`;
  document.getElementById("rotLabel").textContent = rotToggle.checked ? "선발 로테이션" : "전체 투수진";
}
slider.addEventListener("input", () => { syncLabels(); render(); });
seasonToggle.addEventListener("change", () => { syncLabels(); render(); });
rotToggle.addEventListener("change", () => { syncLabels(); render(); });

// 로고 범례 (색상 대신 로고, 클릭 토글)
const legendEl = document.getElementById("trendLegend");
trendChart.data.datasets.forEach((ds, i) => {
  const item = document.createElement("div");
  item.className = "lg-item";
  item.innerHTML = `<img src="${DATA.logos[ds.code]}" alt=""><span>${ds.label}</span>`;
  item.onclick = () => {
    const m = trendChart.getDatasetMeta(i); m.hidden = !m.hidden;
    item.classList.toggle("off"); trendChart.update();
  };
  legendEl.appendChild(item);
});

syncLabels();
render();   // 최초 렌더
</script>
</body>
</html>
"""
