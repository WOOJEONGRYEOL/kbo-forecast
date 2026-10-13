# -*- coding: utf-8 -*-
"""
postseason_dashboard.py — 가을야구(포스트시즌) 페이지 렌더.
대진표 + 라운드별 시리즈 현황·승리확률(몬테카를로) + 경기별 기대스코어 카드.
경기 카드·로고·테마는 dashboard 모듈을 재사용한다.
"""
from pathlib import Path

import config
import dashboard
import postseason as ps

_FMT_LABEL = {"wc": "단판제(4위 1승·5위 2승)", "bo5": "5전 3선승", "bo7": "7전 4선승"}

_TEMPLATE = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>가을야구 · GPT 베이스볼</title>
<style>
  :root{--bg:#0e1117;--card:#161b25;--line:#232a38;--text:#e8ecf3;--muted:#8a94a8;--green:#3ecf8e;--gold:#ffcf6b;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font-family:"Apple SD Gothic Neo","Noto Sans KR",sans-serif;word-break:keep-all;}
  .wrap{max-width:1180px;margin:0 auto;padding:20px 18px 60px;}
  nav{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
  nav a{text-decoration:none;color:var(--muted);background:var(--card);border:1px solid var(--line);border-radius:20px;padding:7px 14px;font-size:14px;}
  nav a.active{color:#0e1117;background:var(--gold);border-color:var(--gold);font-weight:700;}
  h1{font-size:23px;margin:6px 0 2px;}
  .sub{color:var(--muted);font-size:13px;margin-bottom:16px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:16px;}
  .rtitle{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:2px;}
  .rtitle h2{font-size:18px;margin:0;}
  .rfmt{color:var(--muted);font-size:12px;}
  .match{display:flex;align-items:center;gap:10px;font-size:16px;font-weight:700;margin:8px 0 4px;flex-wrap:wrap;}
  .match img{width:26px;height:26px;object-fit:contain;vertical-align:middle;}
  .seed{color:var(--muted);font-weight:600;font-size:12px;}
  .score{font-size:20px;font-weight:800;color:var(--gold);}
  .tbd{color:var(--muted);font-weight:600;}
  .winner{color:var(--green);font-weight:800;}
  .oddsbar{display:flex;height:22px;border-radius:7px;overflow:hidden;font-size:11px;font-weight:700;color:#0e1117;margin:8px 0 4px;}
  .oddsbar>div{display:flex;align-items:center;padding:0 8px;}
  .hint{color:var(--muted);font-size:12px;line-height:1.6;margin:4px 0 8px;}
  .cards-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:8px;}
</style></head><body><div class="wrap">
<nav>
  <a class="home" href="../index.html">🏠</a>
  <a href="dashboard.html">📊 팀 전력</a>
  <a href="players.html">🧢 선수 평가</a>
  <a href="title_race.html">🏁 타이틀 레이스</a>
  <a href="history.html">🏆 역대</a>
  <a href="today.html">🔮 오늘의 승부</a>
  <a href="predictions.html">📈 예측 성적표</a>
  <a class="active" href="postseason.html">🍂 가을야구</a>
</nav>
<h1>🍂 가을야구 — 포스트시즌</h1>
<div class="sub">🕗 최종 갱신 __STAMP__ · 대진은 정규 최종순위로 확정 · 시리즈 승리확률은 몬테카를로(단일경기 정직한 천장 ~56%, 단기전은 분산 큼)</div>
__BODY__
</div></body></html>"""


def _odds_bar(rd) -> str:
    o = rd["odds"]
    if not o:
        return ""
    hi, lo = o["higher_adv"], o["lower_adv"]
    hn, ln = rd["higherName"], rd["lowerName"]
    return (
        f'<div class="oddsbar">'
        f'<div style="width:{max(hi,6)}%;background:var(--green)">{hn} {hi}%</div>'
        f'<div style="width:{max(lo,6)}%;background:#4a90d9">{ln} {lo}%</div></div>'
        f'<div class="hint">시리즈 승리확률(몬테카를로) · 경기별 승률: '
        f'{hn} 홈 {o["p_hi_home"]}% / 원정 {o["p_hi_away"]}%</div>')


def _round_card(rd, logos) -> str:
    hcode, lcode = rd["higher"], rd["lower"]
    hlogo = f'<img src="{logos.get(hcode,"")}" alt="">' if logos.get(hcode) else ""
    # 매치업 줄
    if lcode:
        llogo = f'<img src="{logos.get(lcode,"")}" alt="">' if logos.get(lcode) else ""
        if rd["decided"]:
            tail = f'<span class="winner">→ {rd["winnerName"]} 진출 ✔</span>'
        else:
            tail = f'<span class="score">{rd["higherWins"]} : {rd["lowerWins"]}</span>'
        match = (f'<span class="seed">{rd["higherSeed"]}위</span> {hlogo}{rd["higherName"]} '
                 f'<span class="seed">vs</span> {llogo}{rd["lowerName"]} '
                 f'<span class="seed">{rd["lowerSeed"]}위</span> &nbsp; {tail}')
    else:
        match = (f'<span class="seed">{rd["higherSeed"]}위</span> {hlogo}{rd["higherName"]} '
                 f'<span class="seed">vs</span> <span class="tbd">앞 라운드 승자 (미정)</span>')
    # 경기별 카드
    games_html = ""
    if rd["games"]:
        cards = "".join(dashboard._one_game_card(g, logos) for g in rd["games"])
        games_html = f'<div class="cards-grid">{cards}</div>'
    return (
        f'<div class="card">'
        f'<div class="rtitle"><h2>{rd["name"]}</h2>'
        f'<span class="rfmt">{_FMT_LABEL.get(rd["fmt"],"")}</span></div>'
        f'<div class="match">{match}</div>'
        f'{_odds_bar(rd)}'
        f'{games_html}'
        '</div>')


def save_postseason_page(state: dict, logos=None, path: str = None) -> Path:
    """가을야구 페이지를 data/postseason.html 로 저장. state=postseason.build_postseason()."""
    logos = logos or dashboard.logo_map()
    if not state:
        body = '<div class="card"><p class="hint">포스트시즌 대진이 아직 확정되지 않았습니다(정규시즌 진행 중).</p></div>'
    else:
        body = "".join(_round_card(rd, logos) for rd in state["rounds"])
    stamp = state.get("generated", "") if state else ""
    html = _TEMPLATE.replace("__BODY__", body).replace("__STAMP__", stamp)
    out = Path(path) if path else (Path(config.DATA_DIR) / "postseason.html")
    out.write_text(html, encoding="utf-8")
    return out
