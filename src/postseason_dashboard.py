# -*- coding: utf-8 -*-
"""
postseason_dashboard.py — 가을야구(포스트시즌) 페이지 렌더.
위: 챔피언스리그식 '대진표'(팀명 + 연결선, 최종 관문=한국시리즈가 맨 위).
아래: 활성 시리즈(지금은 와일드카드)부터 매치업·경기별 기대스코어/승률 예측·
      시리즈 승리확률·결과 예측을 상세히. 경기 카드·로고·테마는 dashboard 재사용.
모바일 가로 오버플로 방지: 그리드 minmax(min(100%,300px),1fr), 확률은 텍스트+색바.
"""
from pathlib import Path

import config
import dashboard
import postseason as ps

_FMT_LABEL = {"wc": "단판 승부 · 4위 1승/무 · 5위 2승",
              "bo5": "5전 3선승", "bo7": "7전 4선승"}
_ICON = {"wc": "🪙", "spo": "⚔️", "po": "⚔️", "ks": "🏆"}


_TEMPLATE = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>가을야구 · GPT 베이스볼</title>
<style>
  :root{--bg:#0e1117;--card:#161b25;--line:#232a38;--text:#e8ecf3;--muted:#8a94a8;--green:#3ecf8e;--blue:#4a90d9;--gold:#ffcf6b;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font-family:"Apple SD Gothic Neo","Noto Sans KR",sans-serif;word-break:keep-all;}
  .wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px;overflow-x:hidden;}
  nav{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
  nav a{text-decoration:none;color:var(--muted);background:var(--card);border:1px solid var(--line);border-radius:20px;padding:7px 14px;font-size:14px;}
  nav a.active{color:#0e1117;background:var(--gold);border-color:var(--gold);font-weight:700;}
  h1{font-size:22px;margin:6px 0 2px;}
  .sub{color:var(--muted);font-size:13px;margin-bottom:14px;line-height:1.5;}

  /* 대진표(챔스식) — 최종이 위, 아래로 내려갈수록 먼저 치르는 라운드. 연결선으로 묶음 */
  .bk{display:flex;flex-direction:column;align-items:stretch;margin:2px 0 18px;}
  .bk-node{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:9px 12px;
    display:flex;align-items:center;gap:9px;flex-wrap:wrap;}
  .bk-node.ks{border-color:var(--gold);box-shadow:0 0 0 1px rgba(255,207,107,.22);}
  .bk-node.live{border-color:var(--green);}
  .bk-rn{font-size:11px;color:var(--muted);font-weight:700;white-space:nowrap;display:flex;align-items:center;gap:4px;}
  .bk-mu{font-size:14px;font-weight:700;display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
  .bk-mu img{width:18px;height:18px;object-fit:contain;vertical-align:middle;}
  .bk-mu .vs{color:var(--muted);font-weight:600;font-size:12px;}
  .bk-mu .seed{color:var(--muted);font-weight:600;font-size:11px;}
  .bk-mu .tbd{color:var(--muted);font-weight:600;}
  .bk-mu .win{color:var(--green);font-weight:800;}
  .bk-mu .sc{color:var(--gold);font-weight:800;}
  .bk-link{width:2px;height:16px;background:linear-gradient(var(--line),var(--muted));align-self:center;}

  /* 상세 */
  h2.sec{font-size:16px;margin:18px 0 8px;}
  .rung{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px;margin-bottom:14px;}
  .rung.live{border-color:var(--green);}
  .step{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:8px;}
  .step .ic{font-size:17px;} .step h3{font-size:16px;margin:0;} .step .fmt{color:var(--muted);font-size:11.5px;}
  .mu{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-weight:700;font-size:15px;}
  .mu img{width:22px;height:22px;object-fit:contain;vertical-align:middle;}
  .mu .seed{color:var(--muted);font-weight:600;font-size:11.5px;} .mu .vs{color:var(--muted);font-weight:600;}
  .mu .sc{font-size:18px;font-weight:800;color:var(--gold);} .win{color:var(--green);font-weight:800;}
  .pred{margin:9px 0 2px;font-size:13.5px;font-weight:700;} .pred .q{color:var(--gold);}
  .pbar{display:flex;height:8px;border-radius:5px;overflow:hidden;margin:6px 0 2px;background:var(--line);}
  .pbar>span{display:block;height:100%;}
  .podds{color:var(--muted);font-size:12px;margin-top:3px;line-height:1.5;}
  .games{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:12px;margin-top:10px;}
  .ghint{color:var(--muted);font-size:12px;margin:8px 0 2px;}
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
<h1>🍂 가을야구 — 포스트시즌 대진표</h1>
<div class="sub">🕗 최종 갱신 __STAMP__ · 대진은 정규 최종순위로 확정, 맨 위가 한국시리즈<br>
시리즈 확률은 경기별 승률의 몬테카를로 — 단기전은 분산이 큽니다(단일경기 천장 ~56%).</div>
__BODY__
</div></body></html>"""


def _mu_compact(rd, logos):
    hcode, lcode = rd["higher"], rd["lower"]
    hl = f'<img src="{logos.get(hcode,"")}" alt="">' if logos.get(hcode) else ""
    if not lcode:
        return (f'<span class="seed">{rd["higherSeed"]}</span>{hl}{rd["higherName"]} '
                f'<span class="vs">vs</span> <span class="tbd">승자</span>')
    ll = f'<img src="{logos.get(lcode,"")}" alt="">' if logos.get(lcode) else ""
    base = (f'<span class="seed">{rd["higherSeed"]}</span>{hl}{rd["higherName"]} '
            f'<span class="vs">vs</span> {ll}{rd["lowerName"]}<span class="seed">{rd["lowerSeed"]}</span>')
    if rd["decided"]:
        return base + f' <span class="win">▸ {rd["winnerName"]}</span>'
    if rd["higherWins"] or rd["lowerWins"]:
        return base + f' <span class="sc">{rd["higherWins"]}:{rd["lowerWins"]}</span>'
    return base


def _bracket(rounds, logos):
    """최종(한국시리즈) 위 → 와일드카드 아래, 연결선으로 묶은 컴팩트 대진표."""
    order = list(reversed(rounds))     # ks, po, spo, wc
    parts = []
    for i, rd in enumerate(order):
        live = bool(rd["games"]) and not rd["decided"]
        cls = "bk-node" + (" ks" if rd["key"] == "ks" else "") + (" live" if live else "")
        parts.append(
            f'<div class="{cls}"><span class="bk-rn">{_ICON.get(rd["key"],"")} {rd["name"]}</span>'
            f'<span class="bk-mu">{_mu_compact(rd, logos)}</span></div>')
        if i < len(order) - 1:
            parts.append('<div class="bk-link"></div>')
    return '<div class="bk">' + "".join(parts) + '</div>'


def _pred_block(rd):
    o = rd["odds"]
    goal = "우승" if rd["key"] == "ks" else "진출"
    if rd["decided"]:
        return f'<div class="pred"><span class="q">🔮 결과</span> — {rd["winnerName"]} {goal} 확정 ✔</div>'
    if not o:
        return '<div class="podds">상대가 정해지면 시리즈 예측이 표시됩니다.</div>'
    hi, lo = o["higher_adv"], o["lower_adv"]
    fav, favp = (rd["higherName"], hi) if hi >= lo else (rd["lowerName"], lo)
    note = " · 잠실 공유로 홈 이점 감쇠 반영" if o.get("shared_park") else ""
    return (
        f'<div class="pred"><span class="q">🔮 시리즈 예측</span> — {fav} {goal} <b>{favp}%</b></div>'
        f'<div class="pbar"><span style="width:{hi}%;background:var(--green)"></span>'
        f'<span style="width:{lo}%;background:var(--blue)"></span></div>'
        f'<div class="podds">{rd["higherName"]} {hi}% · {rd["lowerName"]} {lo}% '
        f'<span style="opacity:.8">(경기별 승률 {rd["higherName"]} 홈 {o["p_hi_home"]}%/원정 {o["p_hi_away"]}%{note})</span></div>')


def _detail(rd, logos):
    hcode, lcode = rd["higher"], rd["lower"]
    hl = f'<img src="{logos.get(hcode,"")}" alt="">' if logos.get(hcode) else ""
    ll = f'<img src="{logos.get(lcode,"")}" alt="">' if (lcode and logos.get(lcode)) else ""
    mu = (f'<span class="seed">{rd["higherSeed"]}위</span> {hl}{rd["higherName"]} '
          f'<span class="vs">vs</span> {ll}{rd["lowerName"]} <span class="seed">{rd["lowerSeed"]}위</span>')
    if rd["decided"]:
        mu += f' <span class="win">→ {rd["winnerName"]} 진출 ✔</span>'
    elif rd["higherWins"] or rd["lowerWins"]:
        mu += f' <span class="sc">{rd["higherWins"]} : {rd["lowerWins"]}</span>'
    games_html = ""
    if rd["games"]:
        cards = "".join(dashboard._one_game_card(g, logos) for g in rd["games"])
        games_html = (f'<div class="ghint">경기별 기대 스코어·승률 (완료 경기 + 다음 경기만 · '
                      f'이후 경기는 결과 확정 후)</div><div class="games">{cards}</div>')
    live = bool(rd["games"]) and not rd["decided"]
    return (
        f'<div class="rung{" live" if live else ""}">'
        f'<div class="step"><span class="ic">{_ICON.get(rd["key"],"")}</span>'
        f'<h3>{rd["name"]}</h3><span class="fmt">{_FMT_LABEL.get(rd["fmt"],"")}</span></div>'
        f'<div class="mu">{mu}</div>{_pred_block(rd)}{games_html}</div>')


def save_postseason_page(state: dict, logos=None, path: str = None) -> Path:
    logos = logos or dashboard.logo_map()
    if not state:
        body = '<div class="rung"><div class="podds">포스트시즌 대진이 아직 확정되지 않았습니다(정규시즌 진행 중).</div></div>'
    else:
        rounds = state["rounds"]
        bracket = _bracket(rounds, logos)
        # 상세: 상대가 정해진 라운드(활성/종료)만, 먼저 치르는 순서(와일드카드부터).
        active = [rd for rd in rounds if rd["lower"]]
        detail = ("<h2 class=\"sec\">시리즈 상세 · 경기별 예측</h2>"
                  + "".join(_detail(rd, logos) for rd in active)) if active else ""
        body = bracket + detail
    stamp = state.get("generated", "") if state else ""
    html = _TEMPLATE.replace("__BODY__", body).replace("__STAMP__", stamp)
    out = Path(path) if path else (Path(config.DATA_DIR) / "postseason.html")
    out.write_text(html, encoding="utf-8")
    return out
