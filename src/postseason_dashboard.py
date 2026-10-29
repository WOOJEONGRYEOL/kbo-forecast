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

  /* 대진표 — 왼쪽 '줄기'에 라운드가 가지처럼 합류해 맨 위 트로피로 수렴(토너먼트 느낌).
     위=한국시리즈, 아래로 갈수록 먼저 치르는 라운드. */
  .bk{position:relative;margin:4px 0 20px;padding-left:30px;}
  .bk::before{content:"";position:absolute;left:11px;top:34px;bottom:18px;width:2px;
    background:linear-gradient(var(--muted),var(--gold));border-radius:2px;}
  .bk-apex{position:relative;margin:0 0 14px -30px;display:flex;justify-content:center;}
  .bk-apex::after{content:"";position:absolute;left:11px;bottom:-14px;width:2px;height:14px;background:var(--gold);}
  .bk-apex .cup{background:rgba(255,207,107,.12);border:1px solid var(--gold);color:var(--gold);
    font-weight:800;font-size:13px;border-radius:999px;padding:5px 15px;white-space:nowrap;
    display:inline-flex;align-items:center;gap:6px;}
  .bk-apex .cup img{width:17px;height:17px;object-fit:contain;}
  .bk-row{position:relative;margin:0 0 12px;}
  .bk-row:last-child{margin-bottom:0;}
  .bk-row::before{content:"";position:absolute;left:-19px;top:50%;width:17px;height:15px;
    border-left:2px solid var(--muted);border-bottom:2px solid var(--muted);
    border-bottom-left-radius:9px;transform:translateY(-15px);}
  .bk-row::after{content:"";position:absolute;left:-23px;top:calc(50% - 4px);width:8px;height:8px;
    border-radius:50%;background:var(--gold);box-shadow:0 0 0 3px var(--bg);}
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
  <a class="home" href="../index.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><path d="M4 11 L12 4 L20 11"/><path d="M6 10 V20 H18 V10"/></svg></a>
  <a href="dashboard.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><rect x="4" y="13" width="4" height="7" rx="1"/><rect x="10" y="9" width="4" height="11" rx="1"/><rect x="16" y="5" width="4" height="15" rx="1"/></svg>팀 전력</a>
  <a href="players.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><polygon points="12,3 20.5,9.2 17.2,19.2 6.8,19.2 3.5,9.2"/><circle fill="currentColor" stroke="none" cx="12" cy="12.4" r="1.5"/></svg>선수 평가</a>
  <a href="title_race.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><path d="M6 3 V21"/><path d="M6 4.5 H19 V13.5 H6"/><g fill="currentColor" stroke="none"><rect x="6" y="4.5" width="3.25" height="3"/><rect x="12.5" y="4.5" width="3.25" height="3"/><rect x="9.25" y="7.5" width="3.25" height="3"/><rect x="15.75" y="7.5" width="3.25" height="3"/><rect x="6" y="10.5" width="3.25" height="3"/><rect x="12.5" y="10.5" width="3.25" height="3"/></g></svg>타이틀 레이스</a>
  <a href="history.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><path d="M8 4 H16 V7.5 A4 4 0 0 1 8 7.5 Z"/><path d="M8 4.8 H5.6 A2 2 0 0 0 8 7.4"/><path d="M16 4.8 H18.4 A2 2 0 0 1 16 7.4"/><path d="M12 11.5 V15"/><path d="M10.4 20 L11 15 H13 L13.6 20"/><path d="M9 20 H15"/></svg>역대</a>
  <a href="today.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M20.5 20.5 L15.9 15.9"/></svg>오늘의 승부</a>
  <a href="predictions.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><rect x="5" y="4.5" width="14" height="16" rx="2"/><path d="M9.2 4.5 V3.4 A1.1 1.1 0 0 1 10.3 2.3 H13.7 A1.1 1.1 0 0 1 14.8 3.4 V4.5 Z"/><polyline points="8.5,13 11,15.5 15.5,10"/></svg>예측 성적표</a>
  <a class="active" href="postseason.html"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em;margin-right:5px"><path d="M3 4.5 H7"/><path d="M3 9.5 H7"/><path d="M7 4.5 V9.5"/><path d="M7 7 H11"/><path d="M3 14.5 H7"/><path d="M3 19.5 H7"/><path d="M7 14.5 V19.5"/><path d="M7 17 H11"/><path d="M11 7 V17"/><path d="M11 12 H15"/><circle fill="currentColor" stroke="none" cx="18.5" cy="12" r="2.5"/></svg>가을야구</a>
</nav>
<h1><svg viewBox="0 0 24 24" width="0.92em" height="0.92em" fill="none" stroke="#3ecf8e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.12em;margin-right:6px"><path d="M3 4.5 H7"/><path d="M3 9.5 H7"/><path d="M7 4.5 V9.5"/><path d="M7 7 H11"/><path d="M3 14.5 H7"/><path d="M3 19.5 H7"/><path d="M7 14.5 V19.5"/><path d="M7 17 H11"/><path d="M11 7 V17"/><path d="M11 12 H15"/><circle fill="#3ecf8e" stroke="none" cx="18.5" cy="12" r="2.5"/></svg>가을야구 — 포스트시즌 대진표</h1>
<div class="sub">🕗 최종 갱신 __STAMP__ · 대진은 정규 최종순위로 확정, 맨 위가 한국시리즈<br>
시리즈 확률은 경기별 승률의 몬테카를로 — 단기전은 분산이 큽니다(단일경기 천장 ~56%).</div>
__BODY__
</div></body></html>"""


def _mu_compact(rd, logos):
    hcode, lcode = rd["higher"], rd["lower"]
    hl = f'<img src="{logos.get(hcode,"")}" alt="">' if logos.get(hcode) else ""
    if not lcode:
        return (f'{hl}{rd["higherName"]} '
                f'<span class="vs">vs</span> <span class="tbd">승자</span>')
    ll = f'<img src="{logos.get(lcode,"")}" alt="">' if logos.get(lcode) else ""
    base = (f'{hl}{rd["higherName"]} '
            f'<span class="vs">vs</span> {ll}{rd["lowerName"]}')
    if rd["decided"]:
        return base + f' <span class="win">▸ {rd["winnerName"]}</span>'
    if rd["higherWins"] or rd["lowerWins"]:
        return base + f' <span class="sc">{rd["higherWins"]}:{rd["lowerWins"]}</span>'
    return base


def _bracket(rounds, logos):
    """왼쪽 줄기에 라운드가 가지처럼 합류 → 맨 위 트로피로 수렴하는 토너먼트 대진표.
       순서: 한국시리즈(위) → 와일드카드(아래)."""
    order = list(reversed(rounds))     # ks, po, spo, wc
    # 트로피 꼭지(수렴점) — 우승 확정 팀이 있으면 로고·이름, 아니면 '한국시리즈 우승'.
    ks = next((r for r in rounds if r["key"] == "ks"), None)
    if ks and ks.get("decided") and ks.get("winner"):
        wl = logos.get(ks["winner"], "")
        wimg = f'<img src="{wl}" alt="">' if wl else ""
        cup = f'🏆 {wimg}{ks["winnerName"]} 우승 ✔'
    else:
        cup = '🏆 한국시리즈 우승'
    apex = f'<div class="bk-apex"><span class="cup">{cup}</span></div>'
    rows = []
    for rd in order:
        live = bool(rd["games"]) and not rd["decided"]
        cls = "bk-node" + (" ks" if rd["key"] == "ks" else "") + (" live" if live else "")
        rows.append(
            f'<div class="bk-row"><div class="{cls}">'
            f'<span class="bk-rn">{_ICON.get(rd["key"],"")} {rd["name"]}</span>'
            f'<span class="bk-mu">{_mu_compact(rd, logos)}</span></div></div>')
    return '<div class="bk">' + apex + "".join(rows) + '</div>'


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
    mu = (f'{hl}{rd["higherName"]} '
          f'<span class="vs">vs</span> {ll}{rd["lowerName"]}')
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
        # 상세: 상대가 정해진 라운드(활성/종료)만. 대진표와 같이 상위 스테이지를 위로
        #   쌓아 최신 라운드·결과를 스크롤 없이 먼저 보이게 한다(한국시리즈→와일드카드).
        active = [rd for rd in reversed(rounds) if rd["lower"]]
        detail = ("<h2 class=\"sec\">시리즈 상세 · 경기별 예측</h2>"
                  + "".join(_detail(rd, logos) for rd in active)) if active else ""
        body = bracket + detail
    stamp = state.get("generated", "") if state else ""
    html = _TEMPLATE.replace("__BODY__", body).replace("__STAMP__", stamp)
    out = Path(path) if path else (Path(config.DATA_DIR) / "postseason.html")
    out.write_text(html, encoding="utf-8")
    return out
