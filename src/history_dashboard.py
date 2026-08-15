# -*- coding: utf-8 -*-
"""
history_dashboard.py — 역대 탭(data/history.html) 생성기
=========================================================

data/history_batters.csv·history_pitchers.csv(build_history.py 산출)를 임베드해,
브라우저에서 시즌·포지션 순위와 통산 GOAT를 필터링한다.

- 시즌·포지션 순위: 각 행이 선수-시즌이라 합산 불필요 → 정확.
- 통산 GOAT: 선수 고유 ID(pno)로 합산 → 동명이인 정확 분리.
"""

import csv
import json
from pathlib import Path

import config

# 시대 약자 → 읽는 이름 (역대 팀 범례)
TEAM_LEGEND = {
    "삼": "삼성", "롯": "롯데", "L": "LG", "한": "한화", "두": "두산",
    "S": "SK/SSG", "KIA": "KIA", "키": "키움/넥센/우리", "N": "NC",
    "O": "OB", "KT": "KT", "해": "해태", "현": "현대", "태": "태평양",
    "빙": "빙그레", "쌍": "쌍방울", "M": "MBC", "청": "청보", "삼미": "삼미",
}


def _load(name):
    p = Path(config.DATA_DIR) / name
    if not p.exists():
        return []
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def _record_chase(bats, pits):
    """역대 통산 1위 기록 vs 현역 1위 기록의 격차. (career = pno별 시즌 합)
    각 지표: (컬럼, 라벨, 소수자릿수). WAR만 float(1자리), 나머지 카운팅(0)."""
    MB = [("war", "통산 WAR", 1), ("hit", "통산 안타", 0), ("hr", "통산 홈런", 0),
          ("rbi", "통산 타점", 0), ("run", "통산 득점", 0), ("sb", "통산 도루", 0),
          ("pa", "통산 타석", 0)]
    MP = [("war", "통산 WAR", 1), ("win", "통산 승", 0), ("so", "통산 탈삼진", 0),
          ("sv", "통산 세이브", 0), ("hd", "통산 홀드", 0)]
    cur = max([int(r["season"]) for r in bats] + [int(r["season"]) for r in pits],
              default=0)

    def agg(rows, keys):
        car, meta = {}, {}
        for r in rows:
            key = r.get("pno") or r["name"]
            c = car.setdefault(key, {k: 0.0 for k in keys})
            for k in keys:
                c[k] += float(r.get(k, 0) or 0)
            s = int(r["season"])
            if key not in meta or s >= meta[key][0]:
                meta[key] = (s, r["name"], r["team"], r["color"])
        return car, meta

    out = []
    for rows, M, tag in ((bats, MB, "타자"), (pits, MP, "투수")):
        keys = [k for k, _, _ in M]
        car, meta = agg(rows, keys)
        if not car:
            continue
        for k, label, dec in M:
            fmt = (lambda v: round(v, dec)) if dec else (lambda v: int(round(v)))
            at_key = max(car, key=lambda key: car[key][k])       # 역대 1위
            act = [(key, car[key][k]) for key in car if meta[key][0] == cur]
            if not act:
                continue
            act_key, act_val = max(act, key=lambda x: x[1])       # 현역 1위
            out.append({
                "label": f"{label}({tag})" if k == "war" else label,
                "atName": meta[at_key][1], "atVal": fmt(car[at_key][k]),
                "actName": meta[act_key][1], "actTeam": meta[act_key][2],
                "actColor": meta[act_key][3], "actVal": fmt(act_val),
                "gap": fmt(car[at_key][k] - act_val), "dec": dec,
                "activeIsTop": meta[at_key][0] == cur,   # 역대 1위가 현역이면 경신 중
            })
    return out


def save_history() -> Path:
    bats = _load("history_batters.csv")
    pits = _load("history_pitchers.csv")

    def bnum(r):   # 타자 행 → 컴팩트 배열 (JS의 B 인덱스와 순서 일치)
        return [int(r["season"]), r.get("pno", ""), r["name"], r["team"],
                r["color"], r["pos"], float(r["war"]), float(r["owar"]),
                float(r["dwar"]), float(r["wrcplus"]), int(r["pa"]),
                int(r["hr"]), float(r["ops"]),
                int(r["sb"]), int(r["rbi"]), int(r["hit"]), int(r["run"]),
                float(r["iso"]), float(r["kpct"]), float(r["bbpct"]), float(r["sbrate"]),
                r.get("arch", "")]

    def pnum(r):   # 투수 행 → 컴팩트 배열 (JS의 P 인덱스와 순서 일치)
        return [int(r["season"]), r.get("pno", ""), r["name"], r["team"],
                r["color"], r["role"], float(r["war"]), float(r["era"]),
                float(r["fip"]), float(r["ip"]), int(r["so"]), int(r["gs"]),
                int(r["sv"]), int(r["hd"]), int(r["win"]),
                float(r["k9"]), float(r["bb9"]), float(r["hr9"]),
                r.get("arch", "")]

    seasons = sorted({int(r["season"]) for r in bats} |
                     {int(r["season"]) for r in pits})
    payload = {
        "seasons": seasons,
        "legend": TEAM_LEGEND,
        "batters": [bnum(r) for r in bats],
        "pitchers": [pnum(r) for r in pits],
        "recordChase": _record_chase(bats, pits),
    }
    html = _TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                                    separators=(",", ":")))
    out = Path(config.DATA_DIR) / "history.html"
    out.write_text(html, encoding="utf-8")
    return out


_TEMPLATE = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KBO 역대 기록</title>
<style>
  :root { --bg:#0e1117; --card:#161b25; --line:#232a38; --text:#e8ecf3;
    --muted:#8a94a8; --green:#3ecf8e; --blue:#4a90d9; }
  * { box-sizing:border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--text);
    font-family:"Apple SD Gothic Neo","Noto Sans KR",sans-serif; }
  .wrap { max-width:100%; margin:0; }
  .nav { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; align-items:center; }
  .nav a { text-decoration:none; padding:7px 14px; border-radius:999px; font-size:13px;
    font-weight:600; border:1px solid var(--line); color:var(--muted); background:var(--card); }
  .nav a:hover { color:var(--text); border-color:#3a4560; }
  .nav a.home { font-weight:400; padding:7px 12px; }
  .nav a.active { background:var(--green); color:#0b0e14; border-color:var(--green); }
  h1 { font-size:20px; margin:6px 0; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center;
    background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:12px; margin-bottom:14px; }
  select, .seg button { background:#0d1119; color:var(--text); border:1px solid #33405a;
    border-radius:8px; padding:7px 11px; font-size:13px; font-family:inherit; cursor:pointer; }
  .seg { display:inline-flex; gap:4px; flex-wrap:wrap; }
  .seg button.on { background:var(--green); color:#0b0e14; border-color:var(--green); font-weight:700; }
  label { font-size:12px; color:var(--muted); margin-right:4px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { padding:8px 9px; text-align:right; border-bottom:1px solid var(--line); white-space:nowrap; }
  th:nth-child(2),td:nth-child(2) { text-align:left; }
  th { color:var(--muted); font-weight:600; cursor:pointer; user-select:none; position:relative; }
  th.sorted { color:var(--green); }
  th[data-tip] { text-decoration:underline dotted #5b647a; text-underline-offset:3px; }
  th[data-tip]:hover::after { content:attr(data-tip); position:absolute; left:50%; transform:translateX(-50%);
    top:150%; width:min(240px,70vw); white-space:normal; text-align:left; font-weight:400;
    background:#0b0e14; color:var(--text); border:1px solid var(--line); padding:8px 10px;
    border-radius:8px; font-size:12px; line-height:1.5; z-index:30; box-shadow:0 8px 24px rgba(0,0,0,.55); }
  td.rank { color:var(--muted); }
  tr.prow { cursor:pointer; } tr.prow:hover td { background:#1b2230; }
  td.jaws { color:var(--green); font-weight:700; }
  .dhead { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:4px; }
  .dhead .nm { font-size:18px; font-weight:700; }
  .dhead .meta { color:var(--muted); font-size:12px; }
  .dclose { margin-left:auto; cursor:pointer; color:var(--muted); border:1px solid var(--line);
    border-radius:8px; padding:3px 9px; font-size:12px; background:var(--card); }
  .traj { display:flex; align-items:flex-end; gap:3px; height:90px; margin:12px 0 4px; }
  .traj .b { flex:1; min-width:6px; border-radius:2px 2px 0 0; position:relative; }
  .traj .b span { position:absolute; bottom:-16px; left:50%; transform:translateX(-50%);
    font-size:9px; color:#5b647a; white-space:nowrap; }
  .comps { display:flex; flex-wrap:wrap; gap:8px; margin-top:20px; }
  .comp { border:1px solid var(--line); border-radius:9px; padding:7px 11px; font-size:12px; }
  .comp b { font-size:13px; }
  #cardOverlay { display:none; position:fixed; inset:0; background:#000000cc; z-index:50;
    align-items:center; justify-content:center; }
  .cardbox { display:flex; flex-direction:column; align-items:center; gap:12px; }
  .cardbox svg { width:min(88vw,340px); height:auto; box-shadow:0 8px 40px #000; }
  .cardbtns { display:flex; gap:10px; }
  .cardbtns button { padding:9px 18px; border-radius:9px; border:1px solid #33405a;
    background:var(--card); color:var(--text); font-size:14px; font-family:inherit; cursor:pointer; }
  #cardDl { background:var(--green); color:#0b0e14; border-color:var(--green); font-weight:700; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; }
  .hint { color:var(--muted); font-size:12px; margin:4px 0 12px; }
  .warn { color:#ffb454; }
  .legend { color:var(--muted); font-size:11px; margin-top:10px; line-height:1.7; }
  .goat-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; margin-top:6px; }
  .goat { background:#0d1119; border:1px solid var(--line); border-radius:9px; padding:10px 12px; }
  .goat .gf { font-size:11px; color:var(--muted); font-weight:600; }
  .goat .gn { font-size:15px; font-weight:800; margin:2px 0 1px; }
  .goat .gw { font-size:11.5px; color:var(--green); font-weight:600; }
  /* 모바일 탭 툴팁(플로팅) */
  .tip-pop { position:absolute; z-index:100; max-width:min(280px,82vw);
    background:#0b0e14; color:var(--text); border:1px solid var(--line);
    padding:9px 11px; border-radius:8px; font-size:12px; line-height:1.55;
    box-shadow:0 8px 24px rgba(0,0,0,.55); display:none; }
  .pagefoot { color:var(--muted); font-size:11.5px; line-height:1.7; text-align:center;
    margin:32px auto 8px; padding-top:16px; border-top:1px solid var(--line); max-width:720px; }
  .pagefoot b { color:#aab3c5; }
</style></head><body><div class="wrap">
<div class="nav">
  <a class="home" href="../index.html">🏠</a>
  <a href="dashboard.html">📊 팀 전력</a>
  <a href="players.html">🧢 선수 평가</a>
  <a class="active" href="history.html">🏆 역대</a>
</div>
<h1>🏆 KBO 역대 기록</h1>
<div class="sub">1982~현재 전 시즌. 시즌·포지션별 순위와 통산 리더보드. 데이터: Statiz (WAR·wRC+ 등)</div>

<div class="seg" id="view" style="margin-bottom:12px">
  <button class="on" data-w="rank">📊 순위·선수</button><button data-w="trend">📈 리그 진화</button><button data-w="team">🏟️ 팀 전성기</button>
</div>

<div class="controls">
  <div class="seg" id="side"><button class="on" data-v="bat">타자</button><button data-v="pit">투수</button></div>
  <div id="rankControls" style="display:contents">
    <div><label>시즌</label><select id="season"></select></div>
    <div><label>포지션</label><span class="seg" id="pos"></span></div>
    <div><label>유형</label><select id="archsel"></select></div>
    <div><label>정렬</label><select id="sort"></select></div>
    <div id="minfWrap" style="display:none"><label>표본</label><span class="seg" id="minf"><button class="on" data-m="1">규정 이상</button><button data-m="0">전체</button></span></div>
  </div>
  <div id="trendControls" style="display:none"><label>지표</label><span class="seg" id="metric"></span></div>
</div>

<div class="card" id="trendCard" style="display:none">
  <p class="hint" id="trendHint"></p>
  <div id="trendChart" style="overflow-x:auto"></div>
</div>

<div class="card" id="detailCard" style="display:none"></div>

<div class="card" id="teamCard" style="display:none">
  <h2 style="margin:0 0 4px;font-size:16px">🏟️ 팀 역대 최강 시즌 <span style="color:var(--muted);font-weight:400;font-size:12px">— 팀-시즌 선수 WAR 총합</span></h2>
  <p class="hint">한 시즌 팀 소속 선수들의 WAR을 모두 더한 값 = 그해 그 팀의 전력. 타자/투수로도 나눠 봅니다.</p>
  <div style="overflow-x:auto"><table id="teamTbl"><thead></thead><tbody></tbody></table></div>
  <h2 style="margin:22px 0 4px;font-size:16px">👑 프랜차이즈 GOAT <span style="color:var(--muted);font-weight:400;font-size:12px">— 현 구단 계보 기준 통산 WAR 1위</span></h2>
  <p class="hint">각 구단(전신 포함: 해태→KIA, OB→두산, 빙그레→한화, 쌍방울→SSG 등)에서 통산 WAR이 가장 높은 선수.</p>
  <div id="teamGoat" class="goat-grid"></div>
</div>

<div class="card" id="rankCard">
  <p class="hint" id="tableHint"></p>
  <div style="overflow-x:auto"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  <div class="legend" id="legend"></div>
</div>

<div class="card" id="recordCard" style="margin-top:14px">
  <h2>🏁 역대 기록 도전 <span style="color:var(--muted);font-weight:400;font-size:12px">— 역대 통산 1위 vs 현역 1위</span></h2>
  <p class="hint">주요 통산 기록의 <b>역대 1위</b>와 <b>현역 1위</b>, 그리고 격차. <b>🔥 경신중</b>은 현역 선수가 역대 1위 기록을 지금도 늘리고 있다는 뜻. 데이터: Statiz.</p>
  <div class="table-scroll"><table><thead><tr><th>기록</th><th>역대 1위</th><th>현역 1위</th><th>격차</th></tr></thead>
  <tbody id="tb_record"></tbody></table></div>
</div>

<script>
const DATA = __DATA__;
// 컬럼 인덱스 (pno = 선수 고유 ID; 통산 합산 키)
const B = {season:0,pno:1,name:2,team:3,color:4,pos:5,war:6,owar:7,dwar:8,wrc:9,pa:10,hr:11,ops:12,sb:13,rbi:14,hit:15,run:16,iso:17,kpct:18,bbpct:19,sbrate:20,arch:21};
const P = {season:0,pno:1,name:2,team:3,color:4,role:5,war:6,era:7,fip:8,ip:9,so:10,gs:11,sv:12,hd:13,win:14,k9:15,bb9:16,hr9:17,arch:18};
let side="bat", season="통산", pos="전체", sortKey="war", sortDir=-1;
let view="rank", metric="", archF="전체", minFilter=true;
// 통산 비율지표(작은 표본이면 왜곡)엔 최소 표본 필터 적용
const RATE_KEYS=new Set(["wrc","ops","era","fip"]);
// ABS(자동 볼판정) 2024 전면도입 — 존 판정이 직접 좌우하는 지표에만 마커 표시.
// 방어율·피홈런 등은 공 반발계수·득점환경 영향이 커서 제외(인과 과장 방지).
const ABS_METRICS=new Set(["kpct","k9","kbb"]);
const ABS_YEAR=2024;
const MIN_CAREER_PA=1500, MIN_CAREER_IP=300;
// 헤더 호버 툴팁 — 직관적 항목(홈런·탈삼진·이닝·시즌·포지션 등)은 제외
const HTIP={
  "통산WAR":"대체선수 대비 통산 승리기여 합계.",
  "WAR":"대체선수 대비 승리기여. 대체 수준보다 팀에 더 벌어준 승수.",
  "피크7":"베스트 7시즌 WAR 합(전성기 높이). JAWS의 재료.",
  "JAWS":"(통산 WAR + 피크7) ÷ 2. 누적과 전성기를 함께 보는 명예의전당 자격 지표.",
  "wRC+":"파크·리그 보정 득점창출력. 100=리그평균, 130=평균보다 30%↑.",
  "OPS":"출루율 + 장타율. 간단한 종합 공격 지표.",
  "oWAR":"공격(타격+주루)으로 번 WAR.",
  "dWAR":"수비로 번 WAR.",
  "ERA":"평균자책점 = 9 × 자책점 ÷ 이닝. 낮을수록 좋음.",
  "FIP":"수비무관 평균자책. 수비·운을 걷어낸 투수 본연의 실력.",
};
// 리그 진화 지표: [라벨, 시즌집계함수(rows→값), 소수자릿수, 설명]
const TREND_BAT = {
  hr:  ["홈런 (600타석당)", rs=>{let h=0,p=0;rs.forEach(r=>{h+=r[B.hr];p+=r[B.pa];});return p?h/p*600:0;}, 1,
        "리그 전체 홈런/타석 × 600 — 파워 시대의 흥망"],
  ops: ["리그 OPS", rs=>{let o=0,p=0;rs.forEach(r=>{o+=r[B.ops]*r[B.pa];p+=r[B.pa];});return p?o/p:0;}, 3,
        "타석 가중 평균 OPS — 타고투저 지표"],
  kpct:["삼진율 K%", rs=>{let k=0,p=0;rs.forEach(r=>{k+=r[B.kpct]*r[B.pa];p+=r[B.pa];});return p?k/p:0;}, 1,
        "타석 가중 삼진율 — 10%대→20% 육박, '삼진 시대'의 부상"],
  sb:  ["도루 (600타석당)", rs=>{let s=0,p=0;rs.forEach(r=>{s+=r[B.sb];p+=r[B.pa];});return p?s/p*600:0;}, 1,
        "도루/타석 × 600 — 기동력 야구의 흥망(스몰볼→빅볼)"],
};
const TREND_PIT = {
  k9:  ["삼진 (K/9)", rs=>{let k=0,ip=0;rs.forEach(r=>{k+=r[P.so];ip+=r[P.ip];});return ip?k/ip*9:0;}, 2,
        "9이닝당 탈삼진 — 삼진 시대의 도래"],
  era: ["리그 ERA", rs=>{let e=0,ip=0;rs.forEach(r=>{e+=r[P.era]*r[P.ip];ip+=r[P.ip];});return ip?e/ip:0;}, 2,
        "이닝 가중 평균 ERA — 득점 환경"],
  hr9: ["피홈런 HR/9", rs=>{let h=0,ip=0;rs.forEach(r=>{h+=r[P.hr9]*r[P.ip];ip+=r[P.ip];});return ip?h/ip:0;}, 2,
        "9이닝당 피홈런 — 투수가 겪은 파워 시대(2018 정점)"],
  kbb: ["K/BB (탈삼진÷볼넷)", rs=>{let k=0,b=0;rs.forEach(r=>{k+=r[P.so];b+=r[P.bb9]*r[P.ip]/9;});return b?k/b:0;}, 2,
        "탈삼진 ÷ 볼넷 — 스트라이크로 승부하는 파워피처 시대(1.3→2.3)"],
};

const POS_BAT = ["전체","C","1B","2B","3B","SS","LF","CF","RF","DH"];
const POS_PIT = ["전체","선발","불펜"];
// 정렬 옵션(라벨→접근자·방향)
const SORTS_BAT = {war:["WAR",r=>r[B.war],-1], wrc:["wRC+",r=>r[B.wrc],-1], ops:["OPS",r=>r[B.ops],-1],
  hr:["홈런",r=>r[B.hr],-1], rbi:["타점",r=>r[B.rbi],-1], hit:["안타",r=>r[B.hit],-1],
  run:["득점",r=>r[B.run],-1], sb:["도루",r=>r[B.sb],-1], owar:["oWAR",r=>r[B.owar],-1], dwar:["dWAR",r=>r[B.dwar],-1]};
const SORTS_PIT = {war:["WAR",r=>r[P.war],-1], era:["ERA",r=>r[P.era],1], fip:["FIP",r=>r[P.fip],1],
  so:["탈삼진",r=>r[P.so],-1], win:["승",r=>r[P.win],-1], sv:["세이브",r=>r[P.sv],-1],
  hd:["홀드",r=>r[P.hd],-1], ip:["이닝",r=>r[P.ip],-1]};
// 통산에서 '합산'하는 카운팅 스탯 (비율/WAR류는 별도 처리)
const COUNT_BAT = ["hr","rbi","hit","run","sb"];
const COUNT_PIT = ["so","win","sv","hd"];

function el(id){return document.getElementById(id);}
function num(v,d){return (v==null||isNaN(v))?"-":(d!=null?v.toFixed(d):v);}

// 컨트롤 채우기
(function init(){
  const ss=el("season");
  ss.innerHTML='<option value="통산">통산 (합산)</option>'+
    '<option value="역대">역대 단일시즌 TOP</option>'+
    DATA.seasons.slice().reverse().map(y=>`<option value="${y}">${y}</option>`).join("");
  ss.onchange=()=>{season=ss.value; render();};
  el("side").querySelectorAll("button").forEach(b=>b.onclick=()=>{
    side=b.dataset.v; el("side").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));
    pos="전체"; sortKey="war"; buildPos(); buildSort(); buildArch(); buildMetric();
    if(view==="trend") renderTrend(); else render();
  });
  el("view").querySelectorAll("button").forEach(b=>b.onclick=()=>{
    view=b.dataset.w; el("view").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));
    const trend=view==="trend", team=view==="team", rank=view==="rank";
    el("side").style.display=team?"none":"";   // 팀 뷰는 타자·투수 통합 → 토글 숨김
    el("trendCard").style.display=trend?"block":"none";
    el("trendControls").style.display=trend?"block":"none";
    el("teamCard").style.display=team?"block":"none";
    el("rankControls").style.display=rank?"contents":"none";
    el("rankCard").style.display=rank?"block":"none";
    el("detailCard").style.display="none";
    if(trend) renderTrend(); else if(team) renderTeam(); else render();
  });
  el("minf").querySelectorAll("button").forEach(b=>b.onclick=()=>{
    minFilter=b.dataset.m==="1";
    el("minf").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));
    render();
  });
  buildPos(); buildSort(); buildArch(); buildMetric();
  el("legend").innerHTML="팀 약자: "+Object.entries(DATA.legend).map(([k,v])=>`${k}=${v}`).join(" · ");
  render();
})();
function buildPos(){
  const list=side==="bat"?POS_BAT:POS_PIT;
  el("pos").innerHTML=list.map(p=>`<button class="${p===pos?'on':''}" data-p="${p}">${p}</button>`).join("");
  el("pos").querySelectorAll("button").forEach(b=>b.onclick=()=>{pos=b.dataset.p;
    el("pos").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b)); render();});
}
function buildSort(){
  const S=side==="bat"?SORTS_BAT:SORTS_PIT;
  el("sort").innerHTML=Object.entries(S).map(([k,v])=>`<option value="${k}">${v[0]}</option>`).join("");
  el("sort").value=sortKey in S?sortKey:"war";
  el("sort").onchange=()=>{sortKey=el("sort").value; render();};
}
function buildArch(){
  const I=side==="bat"?B:P;
  const src=side==="bat"?DATA.batters:DATA.pitchers;
  const set=[...new Set(src.map(r=>r[I.arch]).filter(Boolean))].sort();
  archF="전체";
  el("archsel").innerHTML='<option value="전체">전체 유형</option>'+
    set.map(a=>`<option value="${a}">${a}</option>`).join("");
  el("archsel").onchange=()=>{archF=el("archsel").value; render();};
}

// ── 리그 진화 (44년 추이) ──
function buildMetric(){
  const M=side==="bat"?TREND_BAT:TREND_PIT;
  if(!(metric in M)) metric=Object.keys(M)[0];
  el("metric").innerHTML=Object.entries(M).map(([k,v])=>`<button class="${k===metric?'on':''}" data-m="${k}">${v[0]}</button>`).join("");
  el("metric").querySelectorAll("button").forEach(b=>b.onclick=()=>{metric=b.dataset.m;
    el("metric").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b)); renderTrend();});
}
function renderTrend(){
  const M=side==="bat"?TREND_BAT:TREND_PIT;
  const [label,fn,dec,desc]=M[metric];
  const I=side==="bat"?B:P;
  const src=side==="bat"?DATA.batters:DATA.pitchers;
  const bySeason={};
  src.forEach(r=>{(bySeason[r[I.season]]=bySeason[r[I.season]]||[]).push(r);});
  const pts=DATA.seasons.map(y=>({season:y, val:fn(bySeason[y]||[])})).filter(p=>p.val>0);
  const absNote=ABS_METRICS.has(metric)
    ? ` <span style="color:#ffb454">· ABS(자동 볼판정) ${ABS_YEAR} 전면도입</span> — 같은 해 피치클럭·시프트 제한·베이스 확대도 함께 도입돼 전후 차이는 복합 효과입니다(ABS 단독 효과 아님).`
    : "";
  el("trendHint").innerHTML=`<b>${label}</b> — ${desc}. 1982~현재 리그 전체 집계.${absNote}`;
  el("trendChart").innerHTML=lineSVG(pts, dec, label);
}
function lineSVG(pts, dec, label){
  const W=880, H=320, mL=52, mR=16, mT=16, mB=34;
  const xs=pts.map(p=>p.season), ys=pts.map(p=>p.val);
  const x0=Math.min(...xs), x1=Math.max(...xs);
  let y0=Math.min(...ys), y1=Math.max(...ys); const pad=(y1-y0)*0.1||1; y0-=pad; y1+=pad;
  const X=s=>mL+(s-x0)/(x1-x0)*(W-mL-mR);
  const Y=v=>H-mB-(v-y0)/(y1-y0)*(H-mT-mB);
  const line=pts.map((p,i)=>`${i?'L':'M'}${X(p.season).toFixed(1)},${Y(p.val).toFixed(1)}`).join(" ");
  const dots=pts.map(p=>`<circle cx="${X(p.season).toFixed(1)}" cy="${Y(p.val).toFixed(1)}" r="2.5" fill="#3ecf8e"><title>${p.season}: ${p.val.toFixed(dec)}</title></circle>`).join("");
  // 각 시즌 값 라벨 — 겹치지 않게 간격 두고(그리디), 마지막 점은 항상 표시
  let vlab="", lastX=-999;
  pts.forEach((p,i)=>{const xx=X(p.season), yy=Y(p.val);
    if(xx-lastX>=38 || i===pts.length-1){
      vlab+=`<text x="${xx.toFixed(1)}" y="${(yy-7).toFixed(1)}" text-anchor="middle" fill="#cfe3ff" font-size="10">${p.val.toFixed(dec)}</text>`;
      lastX=xx;}});
  // 격자·라벨
  let grid="", xlab="";
  for(let g=0;g<=4;g++){const v=y0+(y1-y0)*g/4, yy=Y(v);
    grid+=`<line x1="${mL}" y1="${yy.toFixed(1)}" x2="${W-mR}" y2="${yy.toFixed(1)}" stroke="#222a3a"/>`;
    grid+=`<text x="${mL-6}" y="${(yy+4).toFixed(1)}" text-anchor="end" fill="#8a94a8" font-size="11">${v.toFixed(dec)}</text>`;}
  for(let s=Math.ceil(x0/5)*5;s<=x1;s+=5){const xx=X(s);
    xlab+=`<text x="${xx.toFixed(1)}" y="${H-12}" text-anchor="middle" fill="#8a94a8" font-size="11">${s}</text>`;}
  // ABS 도입 마커(해당 지표에만): 세로 점선 + 도입 이후 옅은 음영 + 라벨
  let abs="";
  if(ABS_METRICS.has(metric) && x0<=ABS_YEAR && ABS_YEAR<=x1){
    const ax=X(ABS_YEAR);
    abs=`<rect x="${ax.toFixed(1)}" y="${mT}" width="${(W-mR-ax).toFixed(1)}" height="${H-mT-mB}" fill="#ffb454" opacity="0.06"/>`
      +`<line x1="${ax.toFixed(1)}" y1="${mT}" x2="${ax.toFixed(1)}" y2="${H-mB}" stroke="#ffb454" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.85"/>`
      +`<text x="${(ax+4).toFixed(1)}" y="${(mT+11).toFixed(1)}" fill="#ffb454" font-size="10">ABS ${ABS_YEAR}~</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:560px;max-width:${W}px">
    ${grid}${abs}${xlab}<path d="${line}" fill="none" stroke="#3ecf8e" stroke-width="2.5"/>${dots}${vlab}</svg>`;
}

function rowsForSeason(){
  const src=side==="bat"?DATA.batters:DATA.pitchers;
  const I=side==="bat"?B:P;
  // 통산·역대(단일시즌 TOP)는 전 시즌, 나머지는 해당 연도만
  let rows = (season==="통산"||season==="역대") ? src : src.filter(r=>r[I.season]==season);
  // 포지션 필터
  if(pos!=="전체"){
    if(side==="bat") rows=rows.filter(r=>r[B.pos]===pos);
    else rows=rows.filter(r=>r[P.role]===pos);
  } else if(side==="bat"){
    rows=rows.filter(r=>r[B.pos]!=="P");  // 타자 표에서 투수(대타 등) 제외
  }
  // 유형 필터: 시즌 모드는 여기서(선수-시즌 단위). 통산은 주 유형으로 집계 후 필터.
  if(archF!=="전체" && season!=="통산") rows=rows.filter(r=>r[I.arch]===archF);
  return rows;
}

// 통산 합산 (선수 고유 ID 기준 — 동명이인 정확 분리)
function career(rows){
  const I=side==="bat"?B:P;
  const m=new Map();
  rows.forEach(r=>{
    const k=r[I.pno]||r[I.name];   // ID 있으면 ID로, 없으면 이름 폴백
    if(!m.has(k)) m.set(k,{name:r[I.name], seasons:new Set(), teams:new Set(), color:r[I.color], rows:[]});
    const o=m.get(k); o.name=r[I.name]; o.seasons.add(r[I.season]); o.teams.add(r[I.team]);
    o.color=r[I.color]; o.rows.push(r);
  });
  return [...m.values()].map(o=>{
    const agg={name:o.name, pno:o.rows[0][I.pno], ns:o.seasons.size, color:o.color, teams:[...o.teams]};
    if(side==="bat"){
      agg.war=o.rows.reduce((s,r)=>s+r[B.war],0);
      agg.owar=o.rows.reduce((s,r)=>s+r[B.owar],0);
      agg.dwar=o.rows.reduce((s,r)=>s+r[B.dwar],0);
      agg.hr=o.rows.reduce((s,r)=>s+r[B.hr],0);
      agg.rbi=o.rows.reduce((s,r)=>s+r[B.rbi],0);
      agg.hit=o.rows.reduce((s,r)=>s+r[B.hit],0);
      agg.run=o.rows.reduce((s,r)=>s+r[B.run],0);
      agg.sb=o.rows.reduce((s,r)=>s+r[B.sb],0);
      agg.pa=o.rows.reduce((s,r)=>s+r[B.pa],0);
      // 통산 wRC+/OPS·스타일지표: PA 가중 평균
      const wavg=k=>agg.pa?o.rows.reduce((s,r)=>s+r[k]*r[B.pa],0)/agg.pa:0;
      agg.wrc=wavg(B.wrc); agg.ops=wavg(B.ops);
      agg.iso=wavg(B.iso); agg.kpct=wavg(B.kpct); agg.bbpct=wavg(B.bbpct); agg.sbrate=wavg(B.sbrate);
      // 주 포지션 = 최빈
      const pc={}; o.rows.forEach(r=>pc[r[B.pos]]=(pc[r[B.pos]]||0)+1);
      agg.pos=Object.entries(pc).sort((a,b)=>b[1]-a[1])[0][0];
    }else{
      agg.war=o.rows.reduce((s,r)=>s+r[P.war],0);
      agg.so=o.rows.reduce((s,r)=>s+r[P.so],0);
      agg.win=o.rows.reduce((s,r)=>s+r[P.win],0);
      agg.sv=o.rows.reduce((s,r)=>s+r[P.sv],0);
      agg.hd=o.rows.reduce((s,r)=>s+r[P.hd],0);
      agg.ip=o.rows.reduce((s,r)=>s+r[P.ip],0);
      const iwavg=k=>agg.ip?o.rows.reduce((s,r)=>s+r[k]*r[P.ip],0)/agg.ip:0;
      agg.era=iwavg(P.era); agg.fip=iwavg(P.fip);
      agg.k9=iwavg(P.k9); agg.bb9=iwavg(P.bb9); agg.hr9=iwavg(P.hr9);
    }
    // JAWS = (통산 WAR + 피크) / 2, 피크 = 베스트7 시즌 WAR 합 (HOF 자격 지표)
    const wars=o.rows.map(r=>r[I.war]).sort((a,b)=>b-a);
    agg.peak=wars.slice(0,7).reduce((s,x)=>s+x,0);
    agg.jaws=(agg.war+agg.peak)/2;
    // 주 유형 = 커리어 최빈 아키타입
    const ac={}; o.rows.forEach(r=>{const a=r[I.arch]; if(a) ac[a]=(ac[a]||0)+1;});
    agg.arch=Object.keys(ac).length?Object.entries(ac).sort((a,b)=>b[1]-a[1])[0][0]:"";
    return agg;
  });
}

// ── 닮은꼴(comps): 정규화 스탯 벡터 최근접 이웃 ──
// 피처: 타자 [wRC+, OPS, WAR, oWAR, dWAR], 투수 [WAR, FIP, K9, IP]
function compFeat(r, isBat){
  return isBat ? [r[B.wrc], r[B.ops], r[B.war], r[B.owar], r[B.dwar]]
    : [r[P.war], r[P.fip], r[P.ip]?r[P.so]*9/r[P.ip]:0, r[P.ip]];
}
const _norm = {bat:null, pit:null};
function normStats(isBat){
  const key=isBat?"bat":"pit";
  if(_norm[key]) return _norm[key];
  const src=isBat?DATA.batters:DATA.pitchers;
  // 표본 필터: 타자 PA>=100, 투수 IP>=20 (노이즈 컷)
  const pool=src.filter(r=>isBat?r[B.pa]>=100:r[P.ip]>=20);
  const F=pool.map(r=>compFeat(r,isBat));
  const n=F[0].length, mean=Array(n).fill(0), std=Array(n).fill(0);
  F.forEach(v=>v.forEach((x,j)=>mean[j]+=x)); mean.forEach((_,j)=>mean[j]/=F.length);
  F.forEach(v=>v.forEach((x,j)=>std[j]+=(x-mean[j])**2));
  std.forEach((_,j)=>std[j]=Math.sqrt(std[j]/F.length)||1);
  _norm[key]={mean,std,pool}; return _norm[key];
}
function findComps(targetRow, isBat, k){
  const {mean,std,pool}=normStats(isBat);
  const I=isBat?B:P;
  const z=v=>compFeat(v,isBat).map((x,j)=>(x-mean[j])/std[j]);
  const tz=z(targetRow), tp=targetRow[I.pno];
  const sorted=pool.filter(r=>r[I.pno]!==tp)
    .map(r=>{const rz=z(r); const d=Math.sqrt(rz.reduce((s,x,j)=>s+(x-tz[j])**2,0)); return {r,d};})
    .sort((a,b)=>a.d-b.d);
  // 선수당 가장 가까운 시즌 하나만(서로 다른 k명)
  const seen=new Set(), out=[];
  for(const c of sorted){ const p=c.r[I.pno]; if(seen.has(p)) continue;
    seen.add(p); out.push(c); if(out.length>=k) break; }
  return out;
}

// ── 선수 상세: 커리어 궤적 + 닮은꼴 ──
function openDetail(pno){
  const isBat=side==="bat", I=isBat?B:P;
  const src=isBat?DATA.batters:DATA.pitchers;
  const mine=src.filter(r=>r[I.pno]===pno).sort((a,b)=>a[I.season]-b[I.season]);
  if(!mine.length) return;
  const name=mine[mine.length-1][I.name];
  const teams=[...new Set(mine.map(r=>r[I.team]))];
  const totWar=mine.reduce((s,r)=>s+r[I.war],0);
  const peak=mine.reduce((a,r)=>r[I.war]>a[I.war]?r:a, mine[0]);
  const maxW=Math.max(...mine.map(r=>Math.abs(r[I.war])),1);
  // 최빈 유형(아키타입)
  const ac={}; mine.forEach(r=>{const a=r[I.arch]; if(a) ac[a]=(ac[a]||0)+1;});
  const arch=Object.entries(ac).sort((a,b)=>b[1]-a[1])[0];
  // 궤적 막대(연도별 WAR)
  const traj=mine.map(r=>{const w=r[I.war], h=Math.max(2,Math.abs(w)/maxW*80);
    const col=w>=0?"#3ecf8e":"#e0555f";
    return `<div class="b" style="height:${h}px;background:${col}" title="${r[I.season]}: WAR ${w.toFixed(2)}"><span>${String(r[I.season]).slice(2)}</span></div>`;
  }).join("");
  // 닮은꼴 = 피크 시즌 기준
  const comps=findComps(peak, isBat, 6).map(c=>{
    const r=c.r; const stat=isBat?`wRC+ ${Math.round(r[B.wrc])}·WAR ${r[B.war].toFixed(1)}`
      :`ERA ${r[P.era].toFixed(2)}·WAR ${r[P.war].toFixed(1)}`;
    return `<div class="comp"><b>${r[I.name]}</b> <span style="color:var(--muted)">${r[I.season]} ${r[I.team]}</span><br><span style="color:var(--muted)">${stat}</span></div>`;
  }).join("");
  const at=arch?` · 유형 <b>${arch[0]}</b>`:"";
  const summary=`통산 WAR <b>${totWar.toFixed(1)}</b> · ${mine.length}시즌 · 피크 ${peak[I.season]}(WAR ${peak[I.war].toFixed(1)}) · 팀 ${teams.join('·')}${at}`;
  // 카드 생성용 데이터 준비
  const wars=mine.map(r=>r[I.war]).slice().sort((a,b)=>b-a);
  const jaws=(totWar+wars.slice(0,7).reduce((s,x)=>s+x,0))/2;
  let cstat=0; if(isBat){let p=0;mine.forEach(r=>{cstat+=r[B.wrc]*r[B.pa];p+=r[B.pa];});cstat=p?cstat/p:0;}
    else{let ip=0;mine.forEach(r=>{cstat+=r[P.era]*r[P.ip];ip+=r[P.ip];});cstat=ip?cstat/ip:0;}
  _cur={name, color:mine[mine.length-1][I.color]||"#2a3345", isBat,
    posrole:isBat?peak[B.pos]:"투수", teams, ns:mine.length, totWar, jaws,
    peak:{season:peak[I.season], war:peak[I.war]}, arch:arch?arch[0]:"",
    stat:cstat, seasons:mine.map(r=>({s:r[I.season], w:r[I.war]})),
    comps:findComps(peak,isBat,3).map(c=>c.r[I.name])};
  const card=el("detailCard");
  card.innerHTML=`<div class="dhead"><span class="nm">${name}</span>
      <span class="meta">${summary}</span>
      <span class="dclose" onclick="makeCard()" style="border-color:var(--green);color:var(--green)">🎴 카드</span>
      <span class="dclose" onclick="document.getElementById('detailCard').style.display='none'">✕ 닫기</span></div>
    <div class="hint">연도별 WAR (막대에 마우스 올리면 값)</div>
    <div class="traj">${traj}</div>
    <div class="hint" style="margin-top:14px">🔍 <b>${peak[I.season]} 시즌과 닮은꼴</b> (피크 기준 최근접)</div>
    <div class="comps">${comps}</div>`;
  card.style.display="block";
  card.scrollIntoView({behavior:"smooth", block:"start"});
}

// ── 선수 카드 생성기 (공유용 SVG → PNG) ──
let _cur=null;
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function cardSVG(d){
  const W=440,H=620,c=d.color||"#2a3345";
  const first=d.seasons[0].s, last=d.seasons[d.seasons.length-1].s;
  const mx=Math.max(...d.seasons.map(s=>Math.abs(s.w)),1);
  const baseY=494, maxH=74, step=(W-64)/d.seasons.length;
  const bw=Math.min(24,step-3);
  // 궤적 막대(피크 해는 금색으로 강조)
  const bars=d.seasons.map((s,i)=>{const h=Math.max(3,Math.abs(s.w)/mx*maxH);
    const x=32+i*step, y=s.w>=0?baseY-h:baseY;
    const col=s.s===d.peak.season?"#ffc94a":(s.w>=0?"#3ecf8e":"#e0555f");
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${col}"/>`;
  }).join("");
  const st=d.isBat?["wRC+",Math.round(d.stat)]:["ERA",d.stat.toFixed(2)];
  const cells=[["시즌",d.ns],[`피크 (${d.peak.season})`,d.peak.war.toFixed(1)],["JAWS",d.jaws.toFixed(1)],st];
  const cw=(W-64)/4;
  const scells=cells.map((cc,i)=>{const x=32+i*cw+cw/2;
    return `<text x="${x}" y="330" text-anchor="middle" fill="#8a94a8" font-size="11">${cc[0]}</text>
      <text x="${x}" y="356" text-anchor="middle" fill="#e8ecf3" font-size="20" font-weight="700">${cc[1]}</text>`;
  }).join("");
  // 팀명 풀네임(범례로 약자→이름)
  const teamNames=d.teams.map(t=>(DATA.legend&&DATA.legend[t])||t).join(" · ");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="'Apple SD Gothic Neo','Noto Sans KR',sans-serif">
    <rect width="${W}" height="${H}" rx="20" fill="#12161f"/>
    <rect width="${W}" height="${H}" rx="20" fill="none" stroke="${c}" stroke-width="3"/>
    <path d="M0 20 Q0 0 20 0 H${W-20} Q${W} 0 ${W} 20 V104 H0 Z" fill="${c}"/>
    <text x="32" y="52" fill="#fff" font-size="30" font-weight="800">${esc(d.name)}</text>
    <text x="32" y="82" fill="#ffffffcc" font-size="13">${esc(teamNames+' · '+d.posrole+(d.arch?' · '+d.arch:''))}</text>
    <text x="${W/2}" y="176" text-anchor="middle" fill="#8a94a8" font-size="14">통산 WAR</text>
    <text x="${W/2}" y="240" text-anchor="middle" fill="#3ecf8e" font-size="64" font-weight="800">${d.totWar.toFixed(1)}</text>
    <line x1="32" y1="286" x2="${W-32}" y2="286" stroke="#232a38"/>
    ${scells}
    <line x1="32" y1="384" x2="${W-32}" y2="384" stroke="#232a38"/>
    <text x="32" y="408" fill="#8a94a8" font-size="12">연도별 WAR · ${first}~${last} <tspan fill="#ffc94a">(●피크 ${d.peak.season})</tspan></text>
    ${bars}
    <line x1="32" y1="${baseY}" x2="${W-32}" y2="${baseY}" stroke="#3a4560" stroke-width="0.5"/>
    <text x="32" y="${baseY+15}" fill="#5b647a" font-size="10">${first}</text>
    <text x="${W-32}" y="${baseY+15}" text-anchor="end" fill="#5b647a" font-size="10">${last}</text>
    <text x="32" y="540" fill="#8a94a8" font-size="12">🔍 닮은꼴</text>
    <text x="32" y="562" fill="#e8ecf3" font-size="15" font-weight="600">${esc(d.comps.join('  ·  '))}</text>
    <text x="32" y="596" fill="#5b647a" font-size="12">⚾ KBO 역대 기록 · 데이터 Statiz</text>
  </svg>`;
}
function makeCard(){
  if(!_cur) return;
  const svg=cardSVG(_cur);
  // 미리보기 오버레이 + PNG 저장
  let ov=document.getElementById("cardOverlay");
  if(!ov){ov=document.createElement("div");ov.id="cardOverlay";document.body.appendChild(ov);}
  ov.innerHTML=`<div class="cardbox">${svg}
    <div class="cardbtns"><button id="cardDl">📥 PNG 저장</button><button id="cardClose">닫기</button></div></div>`;
  ov.style.display="flex";
  document.getElementById("cardClose").onclick=()=>ov.style.display="none";
  document.getElementById("cardDl").onclick=()=>downloadPNG(svg, _cur.name);
}
function downloadPNG(svg, name){
  const blob=new Blob([svg],{type:"image/svg+xml;charset=utf-8"});
  const url=URL.createObjectURL(blob), img=new Image();
  img.onload=()=>{const sc=2,cv=document.createElement("canvas");
    cv.width=440*sc;cv.height=620*sc;const ctx=cv.getContext("2d");ctx.scale(sc,sc);
    ctx.drawImage(img,0,0);URL.revokeObjectURL(url);
    cv.toBlob(b=>{const a=document.createElement("a");a.href=URL.createObjectURL(b);
      a.download=`${name}_카드.png`;a.click();});};
  img.src=url;
}

// ── 팀 전성기 뷰 (C: 팀 역대 최강 시즌 · D: 프랜차이즈 GOAT) ──
// 전신 구단을 현 프랜차이즈로 합침(해태→KIA, OB→두산, MBC→LG, 빙그레→한화, 쌍방울→SSG). 현대 계열은 해체.
const FRANCHISE = {
  "롯":"롯데","롯R":"롯데", "삼":"삼성","삼R":"삼성", "해":"KIA","KIA":"KIA",
  "O":"두산","두":"두산", "M":"LG","L":"LG", "빙":"한화","한":"한화","한R":"한화",
  "쌍":"SSG","쌍R":"SSG","S":"SSG", "N":"NC", "KT":"KT", "키":"키움",
  "삼미":"현대(해체)","청":"현대(해체)","태":"현대(해체)","현":"현대(해체)","현R":"현대(해체)"};
const FR_ORDER=["삼성","롯데","LG","두산","KIA","한화","SSG","키움","NC","KT","현대(해체)"];

function renderTeam(){
  const teamName=t=>DATA.legend[t]||t;
  // 팀-시즌 총 WAR (타자+투수) + 대표선수
  const ts=new Map();
  function acc(rows, I, isBat){
    rows.forEach(r=>{
      const key=r[I.season]+"|"+r[I.team];
      let o=ts.get(key);
      if(!o){ o={season:r[I.season],team:r[I.team],color:r[I.color],bwar:0,pwar:0,bBat:null,bPit:null}; ts.set(key,o); }
      const w=r[I.war];
      if(isBat){ o.bwar+=w; if(!o.bBat || w>o.bBat.w) o.bBat={name:r[I.name], w}; }
      else { o.pwar+=w; if(!o.bPit || w>o.bPit.w) o.bPit={name:r[I.name], w}; }
      if((r[I.color]||'').toLowerCase()!=='#888888') o.color=r[I.color];
    });
  }
  acc(DATA.batters,B,true); acc(DATA.pitchers,P,false);
  const seasons=[...ts.values()].map(o=>({...o,war:o.bwar+o.pwar})).sort((a,b)=>b.war-a.war).slice(0,25);
  const rep=b=>b?`${esc(b.name)} <span style="color:var(--muted)">${num(b.w,1)}</span>`:'-';
  const tt=el("teamTbl");
  tt.querySelector("thead").innerHTML="<tr><th>#</th><th style='text-align:left'>팀 · 시즌</th><th>총 WAR</th>"
    +"<th>타자 WAR</th><th style='text-align:left'>대표 타자</th><th>투수 WAR</th><th style='text-align:left'>대표 투수</th></tr>";
  tt.querySelector("tbody").innerHTML=seasons.map((o,i)=>`<tr><td class="rank">${i+1}</td>`
    +`<td style="text-align:left"><span class="dot" style="background:${o.color||'#888'}"></span>${teamName(o.team)} <b>${o.season}</b></td>`
    +`<td><b>${num(o.war,1)}</b></td>`
    +`<td>${num(o.bwar,1)}</td><td style="text-align:left">${rep(o.bBat)}</td>`
    +`<td>${num(o.pwar,1)}</td><td style="text-align:left">${rep(o.bPit)}</td></tr>`).join("");

  // 프랜차이즈 GOAT: 프랜차이즈별 통산 WAR 1위 (선수 고유 ID로 합산)
  const fr={};
  function accFr(rows,I,isBat){ rows.forEach(r=>{ const f=FRANCHISE[r[I.team]]; if(!f) return;
    const g=fr[f]||(fr[f]={}); const k=(isBat?'b':'p')+(r[I.pno]||r[I.name]);
    const e=g[k]||(g[k]={name:r[I.name],war:0,color:r[I.color],pno:r[I.pno],isBat});
    e.war+=r[I.war]; e.name=r[I.name]; if((r[I.color]||'').toLowerCase()!=='#888888') e.color=r[I.color]; }); }
  accFr(DATA.batters,B,true); accFr(DATA.pitchers,P,false);
  el("teamGoat").innerHTML=FR_ORDER.filter(f=>fr[f]).map(f=>{
    const best=Object.values(fr[f]).sort((a,b)=>b.war-a.war)[0];
    return `<div class="goat" style="border-left:4px solid ${best.color||'#888'};cursor:pointer" title="${esc(best.name)} 상세 보기" data-pno="${best.pno||''}" data-bat="${best.isBat?1:0}">`
      +`<div class="gf">${f}</div><div class="gn">${esc(best.name)}</div>`
      +`<div class="gw">통산 WAR ${num(best.war,1)} ›</div></div>`;
  }).join("");
  el("teamGoat").querySelectorAll(".goat").forEach(g=>{ const p=g.dataset.pno;
    if(p) g.onclick=()=>gotoPlayer(p, g.dataset.bat==="1"); });
}

// GOAT/외부에서 특정 선수 상세로 이동 — side·뷰를 맞추고 순위 뷰에서 상세 열기
function gotoPlayer(pno, isBat){
  side = isBat ? "bat" : "pit";
  pos="전체"; archF="전체"; season="통산"; sortKey="war";
  el("side").querySelectorAll("button").forEach(x=>x.classList.toggle("on", x.dataset.v===side));
  el("season").value="통산";
  buildPos(); buildSort(); buildArch(); buildMetric();
  if(el("archsel")) el("archsel").value="전체";
  view="rank";
  el("view").querySelectorAll("button").forEach(x=>x.classList.toggle("on", x.dataset.w==="rank"));
  el("side").style.display="";
  el("trendCard").style.display="none"; el("trendControls").style.display="none";
  el("teamCard").style.display="none";
  el("rankControls").style.display="contents"; el("rankCard").style.display="block";
  render();
  openDetail(pno);
}

// 유형(아키타입)을 정하는 핵심 지표 설명 — '왜 이 유형인지' 안내
function archDesc(a){
  if(!a) return "";
  if(a.includes("슬러거")) return "장타력(<b>ISO</b>↑)이 핵심 — 홈런·장타 비중이 큰 거포형.";
  if(a.includes("스윙형")) return "삼진(<b>K%</b>↑)이 많은 대신 강한 스윙 — 파워형.";
  if(a.includes("출루형")) return "볼넷(<b>BB%</b>↑)으로 출루하는 선구안형.";
  if(a.includes("호타준족")) return "도루(<b>도루%</b>↑)가 많은 스피드형.";
  if(a.includes("정교")) return "컨택 위주 — 삼진 적고 장타는 평범한 정교한 타자.";
  if(a.includes("수비")||a.includes("백업")) return "타격 생산은 평범, 수비·백업 가치 중심.";
  if(a.includes("파워피처")) return "탈삼진(<b>K/9</b>↑)이 높은 구위형.";
  if(a.includes("이닝이터")) return "선발로 많은 이닝을 소화하는 유형.";
  if(a.includes("제구난")) return "볼넷(<b>BB/9</b>↑)이 많은 제구 불안형.";
  if(a.includes("피홈런")) return "피홈런(<b>HR/9</b>↑)이 많은 유형.";
  if(a.includes("맞춰")) return "삼진보다 맞춰 잡는 불펜형.";
  return "군집 분석으로 스타일이 비슷한 선수끼리 묶은 유형입니다.";
}

function render(){
  let rows=rowsForSeason();
  const isCareer=season==="통산";
  const seasonTop=season==="역대";   // 역대 단일시즌 TOP (선수-시즌 단위 전 역사 랭킹)
  let recs;
  if(isCareer){ recs=career(rows);
    // 통산도 포지션 필터(주포지션 기준)
    if(pos!=="전체" && side==="bat") recs=recs.filter(r=>r.pos===pos);
    if(archF!=="전체") recs=recs.filter(r=>r.arch===archF);   // 주 유형 필터
  } else {
    recs=rows.map(r=>side==="bat"
      ? {season:r[B.season],pno:r[B.pno],name:r[B.name],team:r[B.team],color:r[B.color],pos:r[B.pos],war:r[B.war],owar:r[B.owar],dwar:r[B.dwar],wrc:r[B.wrc],hr:r[B.hr],ops:r[B.ops],pa:r[B.pa],rbi:r[B.rbi],hit:r[B.hit],run:r[B.run],sb:r[B.sb],iso:r[B.iso],kpct:r[B.kpct],bbpct:r[B.bbpct],sbrate:r[B.sbrate]}
      : {season:r[P.season],pno:r[P.pno],name:r[P.name],team:r[P.team],color:r[P.color],role:r[P.role],war:r[P.war],era:r[P.era],fip:r[P.fip],so:r[P.so],ip:r[P.ip],win:r[P.win],sv:r[P.sv],hd:r[P.hd],k9:r[P.k9],bb9:r[P.bb9],hr9:r[P.hr9]});
  }
  // 정렬 (통산 모드엔 JAWS·피크 추가)
  let S=side==="bat"?{...SORTS_BAT}:{...SORTS_PIT};
  if(isCareer){ S={...S, peak:["피크7",r=>r.peak,-1], jaws:["JAWS",r=>r.jaws,-1]}; }
  if(!(sortKey in S)) sortKey="war";
  el("sort").innerHTML=Object.entries(S).map(([k,v])=>`<option value="${k}">${v[0]}</option>`).join("");
  el("sort").value=sortKey;
  // 통산 비율지표(wRC+·OPS·ERA·FIP) 순위는 1~2시즌 소표본이 상위를 왜곡 → 최소 표본 필터
  const showMin = isCareer && RATE_KEYS.has(sortKey);
  el("minfWrap").style.display = showMin ? "" : "none";
  if(showMin && minFilter)
    recs = recs.filter(r => side==="bat" ? r.pa>=MIN_CAREER_PA : r.ip>=MIN_CAREER_IP);
  const dir=S[sortKey][2];
  recs.sort((a,b)=>(a[sortKey]-b[sortKey])*dir);
  recs=recs.slice(0,50);

  // 헤더·행 — 열 정의: [라벨, 정렬키|null, 값(r,i)=>내용, td클래스]
  const thead=el("tbl").querySelector("thead"), tbody=el("tbl").querySelector("tbody");
  const teamCell=r=>`<span class="dot" style="background:${r.color||'#888'}"></span>${r.team||(r.teams&&r.teams.join('·'))||'-'}`;
  const RANK=["#",null,(r,i)=>i+1,"rank"], NAME=["선수","name",r=>r.name], TEAM=["팀",null,teamCell,"tl"];
  const YR=seasonTop?[["시즌",null,r=>r.season]]:[];   // 역대 단일시즌 뷰는 연도 열 추가
  let cols;
  if(side==="bat"){
    cols=isCareer
      ? [RANK,NAME,TEAM,["시즌",null,r=>r.ns+"시즌"],["통산WAR","war",r=>`<b>${num(r.war,1)}</b>`],
         ["JAWS","jaws",r=>num(r.jaws,1),"jaws"],["wRC+","wrc",r=>num(r.wrc,0)],
         ["홈런","hr",r=>r.hr],["타점","rbi",r=>r.rbi],["안타","hit",r=>r.hit],["득점","run",r=>r.run],["도루","sb",r=>r.sb]]
      : [RANK,NAME,TEAM,...YR,["포지션",null,r=>r.pos],["WAR","war",r=>`<b>${num(r.war,2)}</b>`],
         ["wRC+","wrc",r=>num(r.wrc,0)],["OPS","ops",r=>num(r.ops,3)],["홈런","hr",r=>r.hr],
         ["타점","rbi",r=>r.rbi],["도루","sb",r=>r.sb],["oWAR","owar",r=>num(r.owar,2)],["dWAR","dwar",r=>num(r.dwar,2)]];
  }else{
    cols=isCareer
      ? [RANK,NAME,TEAM,["시즌",null,r=>r.ns+"시즌"],["통산WAR","war",r=>`<b>${num(r.war,1)}</b>`],
         ["JAWS","jaws",r=>num(r.jaws,1),"jaws"],["ERA","era",r=>num(r.era,2)],["탈삼진","so",r=>r.so],
         ["승","win",r=>r.win],["세이브","sv",r=>r.sv],["홀드","hd",r=>r.hd]]
      : [RANK,NAME,TEAM,...YR,["역할",null,r=>r.role],["WAR","war",r=>`<b>${num(r.war,2)}</b>`],
         ["ERA","era",r=>num(r.era,2)],["FIP","fip",r=>num(r.fip,2)],["이닝","ip",r=>num(r.ip,1)],
         ["탈삼진","so",r=>r.so],["승","win",r=>r.win],["세이브","sv",r=>r.sv],["홀드","hd",r=>r.hd]];
  }
  // 유형(아키타입) 필터 시: 그 유형을 정하는 세부지표를 열로 추가 → '왜 이 유형인지' 근거 노출
  if(archF!=="전체"){
    cols = side==="bat"
      ? cols.concat([["ISO",null,r=>num(r.iso,3)],["BB%",null,r=>num(r.bbpct,1)],["K%",null,r=>num(r.kpct,1)],["도루%",null,r=>num(r.sbrate,1)]])
      : cols.concat([["K/9",null,r=>num(r.k9,1)],["BB/9",null,r=>num(r.bb9,1)],["HR/9",null,r=>num(r.hr9,1)]]);
  }
  thead.innerHTML="<tr>"+cols.map(c=>{const tp=HTIP[c[0]];
    return `<th data-k="${c[1]||''}"${tp?` data-tip="${tp.replace(/"/g,'&quot;')}"`:''} class="${c[1]===sortKey?'sorted':''}">${c[0]}</th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach(th=>{const k=th.dataset.k; if(k&&S[k]) th.onclick=()=>{sortKey=k; render();};});

  tbody.innerHTML=recs.map((r,i)=>{
    const tds=cols.map(c=>{const v=c[2](r,i); const cls=c[3]?` class="${c[3]}"`:``;
      const style=c[3]==="tl"?` style="text-align:left"`:``; return `<td${cls}${style}>${v}</td>`;}).join("");
    return `<tr class="prow" data-pno="${r.pno||''}">${tds}</tr>`;
  }).join("")||`<tr><td colspan="13" style="text-align:center;color:var(--muted);padding:20px">해당 없음</td></tr>`;
  tbody.querySelectorAll("tr.prow").forEach(tr=>{ const pno=tr.dataset.pno;
    if(pno) tr.onclick=()=>openDetail(pno); });

  const archNote = archF!=="전체"
    ? `<div style="margin-bottom:6px"><b style="color:var(--green)">🏷 ${archF}</b> — ${archDesc(archF)} <span style="color:var(--muted)">오른쪽에 이 유형을 정하는 세부지표를 표시했습니다.</span></div>` : "";
  el("tableHint").innerHTML = archNote + (isCareer
    ? `통산 ${side==="bat"?"타자":"투수"} ${sortKey.toUpperCase()} 순위 (상위 50) · wRC+/ERA 등 비율은 PA·이닝 가중평균.`
      + (pos!=="전체" ? ` <b style="color:#ffb454">${pos} 포지션 순위</b> — <b>${pos}로 뛴 시즌만</b> 합산합니다. 팀·통산기록도 그 시절 기준이라, 다른 포지션으로 뛴 시즌은 빠집니다(예: 우익수→지명타자로 옮긴 선수는 RF 순위엔 RF 시절 팀만, DH 시절은 DH 순위에). 전체 커리어는 포지션을 '전체'로.` : "")
      + (showMin ? (minFilter
          ? ` <b style="color:var(--acc,#3ecf8e)">표본 필터 ON</b> — 통산 ${side==="bat"?MIN_CAREER_PA+"타석":MIN_CAREER_IP+"이닝"} 이상만(1~2시즌 소표본 왜곡 제거). '전체'로 해제 가능.`
          : ` <b style="color:#ffb454">표본 필터 OFF</b> — 소수 시즌 선수가 상위에 낄 수 있습니다.`) : "")
    : seasonTop
      ? `역대 <b>단일 시즌</b> ${side==="bat"?"타자":"투수"} ${sortKey.toUpperCase()} TOP 50 — 1982~현재 전 역사에서 '한 시즌' 최고 기록들. 선수를 클릭하면 커리어 상세로.`
      : `${season} 시즌 ${pos==="전체"?"전체":pos} ${side==="bat"?"타자":"투수"} 순위 (상위 50). 카운팅(홈런 등)은 시즌 경기수(옛 100 vs 현 144)를 감안하세요.`);
}

// 모바일(터치): 지표를 탭하면 정의 표시. 데스크톱(hover 가능)은 CSS 호버 유지.
(function tapTips(){
  if(!window.matchMedia || !matchMedia('(hover: none)').matches) return;
  let pop=null;
  document.addEventListener('click', function(e){
    const t=e.target.closest('[data-tip]');
    if(!t || !t.getAttribute('data-tip')){ if(pop) pop.style.display='none'; return; }
    if(!pop){ pop=document.createElement('div'); pop.className='tip-pop'; document.body.appendChild(pop); }
    if(pop._for===t && pop.style.display==='block'){ pop.style.display='none'; pop._for=null; return; }
    pop._for=t; pop.textContent=t.getAttribute('data-tip'); pop.style.display='block';
    const r=t.getBoundingClientRect();
    pop.style.left=Math.max(8, Math.min(window.innerWidth-8-pop.offsetWidth, r.left+r.width/2-pop.offsetWidth/2))+'px';
    pop.style.top=(window.scrollY+r.bottom+6)+'px';
  }, true);
})();
// ── 역대 기록 도전 (역대 1위 vs 현역 1위) ──
(function recordBoard(){
  const rc = DATA.recordChase || [];
  const card = document.getElementById("recordCard");
  if(!rc.length){ if(card) card.style.display="none"; return; }
  document.getElementById("tb_record").innerHTML = rc.map(r=>{
    const gap = r.activeIsTop
      ? `<span style="color:#3ecf8e;font-weight:700">🔥 경신중</span>`
      : `<b style="color:#ffb454">-${r.gap}</b>`;
    const act = `<span style="color:${r.actColor};font-weight:600">${r.actName}</span> <b>${r.actVal}</b>`;
    return `<tr><td><b>${r.label}</b></td>`
      + `<td>${r.atName} <b>${r.atVal}</b></td>`
      + `<td>${act}</td><td>${gap}</td></tr>`;
  }).join("");
})();
</script>
<footer class="pagefoot">
  <b>자료 출처</b> · 네이버 야구(경기 기록) · KBO Talent(kbostuff.app, 트래킹 지표) · 스태티즈 Statiz(WAR·세부 기록)<br>
  취미·학습 목적의 <b>개인 세이버매트릭스 프로젝트</b>입니다. 상업적 이용을 하지 않으며, 상업적으로 이용할 수 없습니다. 데이터 권리는 각 출처에 있습니다.
</footer>
</div></body></html>
"""
