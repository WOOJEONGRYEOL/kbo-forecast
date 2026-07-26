# -*- coding: utf-8 -*-
"""
history_dashboard.py — 역대 탭(data/history.html) 생성기
=========================================================

data/history_batters.csv·history_pitchers.csv(build_history.py 산출)를 임베드해,
브라우저에서 시즌·포지션 순위와 통산 GOAT를 필터링한다.

- 시즌·포지션 순위: 각 행이 선수-시즌이라 합산 불필요 → 정확.
- 통산 GOAT: 이름으로 합산(현재 PlayerNo 없음) → 동명이인 뭉칠 수 있음(⚠️).
  크롤러 p_no 재크롤 후 정확해짐.
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
    "빙": "빙그레", "쌍": "쌍방울", "M": "MBC", "청": "청보",
}


def _load(name):
    p = Path(config.DATA_DIR) / name
    if not p.exists():
        return []
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def save_history() -> Path:
    bats = _load("history_batters.csv")
    pits = _load("history_pitchers.csv")

    def bnum(r):   # 타자 행 → 컴팩트 배열
        return [int(r["season"]), r["name"], r["team"], r["color"], r["pos"],
                float(r["war"]), float(r["owar"]), float(r["dwar"]),
                float(r["wrcplus"]), int(r["pa"]), int(r["hr"]), float(r["ops"])]

    def pnum(r):
        return [int(r["season"]), r["name"], r["team"], r["color"], r["role"],
                float(r["war"]), float(r["era"]), float(r["fip"]),
                float(r["ip"]), int(r["so"]), int(r["gs"])]

    seasons = sorted({int(r["season"]) for r in bats} |
                     {int(r["season"]) for r in pits})
    payload = {
        "seasons": seasons,
        "legend": TEAM_LEGEND,
        "batters": [bnum(r) for r in bats],
        "pitchers": [pnum(r) for r in pits],
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
  body { margin:0; padding:20px; background:var(--bg); color:var(--text);
    font-family:"Apple SD Gothic Neo","Noto Sans KR",sans-serif; }
  .wrap { max-width:960px; margin:0 auto; }
  .nav { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
  .nav a { text-decoration:none; padding:7px 14px; border-radius:999px; font-size:13px;
    font-weight:600; border:1px solid var(--line); color:var(--muted); background:var(--card); }
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
  th { color:var(--muted); font-weight:600; cursor:pointer; user-select:none; }
  th.sorted { color:var(--green); }
  td.rank { color:var(--muted); }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; }
  .hint { color:var(--muted); font-size:12px; margin:4px 0 12px; }
  .warn { color:#ffb454; }
  .legend { color:var(--muted); font-size:11px; margin-top:10px; line-height:1.7; }
</style></head><body><div class="wrap">
<div class="nav">
  <a href="dashboard.html">📊 팀 전력</a>
  <a href="players.html">🧢 선수 평가</a>
  <a class="active" href="history.html">🏆 역대</a>
</div>
<h1>🏆 KBO 역대 기록</h1>
<div class="sub">1982~현재 전 시즌. 시즌·포지션별 순위와 통산 리더보드. 데이터: Statiz (WAR·wRC+ 등)</div>

<div class="controls">
  <div class="seg" id="side"><button class="on" data-v="bat">타자</button><button data-v="pit">투수</button></div>
  <div><label>시즌</label><select id="season"></select></div>
  <div><label>포지션</label><span class="seg" id="pos"></span></div>
  <div><label>정렬</label><select id="sort"></select></div>
</div>

<div class="card">
  <p class="hint" id="tableHint"></p>
  <div style="overflow-x:auto"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  <div class="legend" id="legend"></div>
</div>

<script>
const DATA = __DATA__;
// 컬럼 인덱스
const B = {season:0,name:1,team:2,color:3,pos:4,war:5,owar:6,dwar:7,wrc:8,pa:9,hr:10,ops:11};
const P = {season:0,name:1,team:2,color:3,role:4,war:5,era:6,fip:7,ip:8,so:9,gs:10};
let side="bat", season="통산", pos="전체", sortKey="war", sortDir=-1;

const POS_BAT = ["전체","C","1B","2B","3B","SS","LF","CF","RF","DH"];
const POS_PIT = ["전체","선발","불펜"];
// 정렬 옵션(라벨→접근자·방향)
const SORTS_BAT = {war:["WAR",r=>r[B.war],-1], wrc:["wRC+",r=>r[B.wrc],-1],
  hr:["홈런",r=>r[B.hr],-1], owar:["oWAR",r=>r[B.owar],-1], dwar:["dWAR",r=>r[B.dwar],-1], ops:["OPS",r=>r[B.ops],-1]};
const SORTS_PIT = {war:["WAR",r=>r[P.war],-1], era:["ERA",r=>r[P.era],1],
  fip:["FIP",r=>r[P.fip],1], so:["탈삼진",r=>r[P.so],-1], ip:["이닝",r=>r[P.ip],-1]};

function el(id){return document.getElementById(id);}
function num(v,d){return (v==null||isNaN(v))?"-":(d!=null?v.toFixed(d):v);}

// 컨트롤 채우기
(function init(){
  const ss=el("season");
  ss.innerHTML='<option value="통산">통산 (합산)</option>'+
    DATA.seasons.slice().reverse().map(y=>`<option value="${y}">${y}</option>`).join("");
  ss.onchange=()=>{season=ss.value; render();};
  el("side").querySelectorAll("button").forEach(b=>b.onclick=()=>{
    side=b.dataset.v; el("side").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));
    pos="전체"; sortKey="war"; buildPos(); buildSort(); render();
  });
  buildPos(); buildSort();
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

function rowsForSeason(){
  const src=side==="bat"?DATA.batters:DATA.pitchers;
  const I=side==="bat"?B:P;
  let rows = season==="통산" ? src : src.filter(r=>r[I.season]==season);
  // 포지션 필터
  if(pos!=="전체"){
    if(side==="bat") rows=rows.filter(r=>r[B.pos]===pos);
    else rows=rows.filter(r=>r[P.role]===pos);
  } else if(side==="bat"){
    rows=rows.filter(r=>r[B.pos]!=="P");  // 타자 표에서 투수(대타 등) 제외
  }
  return rows;
}

// 통산 합산 (이름 기준 — 동명이인 주의)
function career(rows){
  const I=side==="bat"?B:P;
  const m=new Map();
  rows.forEach(r=>{
    const k=r[I.name];
    if(!m.has(k)) m.set(k,{name:k, seasons:new Set(), teams:new Set(), color:r[I.color], rows:[]});
    const o=m.get(k); o.seasons.add(r[I.season]); o.teams.add(r[I.team]); o.rows.push(r);
  });
  return [...m.values()].map(o=>{
    const agg={name:o.name, ns:o.seasons.size, color:o.color, teams:[...o.teams]};
    if(side==="bat"){
      agg.war=o.rows.reduce((s,r)=>s+r[B.war],0);
      agg.owar=o.rows.reduce((s,r)=>s+r[B.owar],0);
      agg.dwar=o.rows.reduce((s,r)=>s+r[B.dwar],0);
      agg.hr=o.rows.reduce((s,r)=>s+r[B.hr],0);
      agg.pa=o.rows.reduce((s,r)=>s+r[B.pa],0);
      // 통산 wRC+/OPS: PA 가중 평균
      agg.wrc=agg.pa?o.rows.reduce((s,r)=>s+r[B.wrc]*r[B.pa],0)/agg.pa:0;
      agg.ops=agg.pa?o.rows.reduce((s,r)=>s+r[B.ops]*r[B.pa],0)/agg.pa:0;
      // 주 포지션 = 최빈
      const pc={}; o.rows.forEach(r=>pc[r[B.pos]]=(pc[r[B.pos]]||0)+1);
      agg.pos=Object.entries(pc).sort((a,b)=>b[1]-a[1])[0][0];
    }else{
      agg.war=o.rows.reduce((s,r)=>s+r[P.war],0);
      agg.so=o.rows.reduce((s,r)=>s+r[P.so],0);
      agg.ip=o.rows.reduce((s,r)=>s+r[P.ip],0);
      const er=o.rows.reduce((s,r)=>s+r[P.era]*r[P.ip],0);
      agg.era=agg.ip?er/agg.ip:0;
      agg.fip=agg.ip?o.rows.reduce((s,r)=>s+r[P.fip]*r[P.ip],0)/agg.ip:0;
    }
    return agg;
  });
}

function render(){
  let rows=rowsForSeason();
  const isCareer=season==="통산";
  let recs;
  if(isCareer){ recs=career(rows);
    // 통산도 포지션 필터(주포지션 기준)
    if(pos!=="전체" && side==="bat") recs=recs.filter(r=>r.pos===pos);
  } else {
    const I=side==="bat"?B:P;
    recs=rows.map(r=>side==="bat"
      ? {name:r[B.name],team:r[B.team],color:r[B.color],pos:r[B.pos],war:r[B.war],owar:r[B.owar],dwar:r[B.dwar],wrc:r[B.wrc],hr:r[B.hr],ops:r[B.ops],pa:r[B.pa]}
      : {name:r[P.name],team:r[P.team],color:r[P.color],role:r[P.role],war:r[P.war],era:r[P.era],fip:r[P.fip],so:r[P.so],ip:r[P.ip]});
  }
  // 정렬
  const S=side==="bat"?SORTS_BAT:SORTS_PIT; const dir=S[sortKey][2];
  recs.sort((a,b)=>(a[sortKey]-b[sortKey])*dir);
  recs=recs.slice(0,50);

  // 헤더·행
  const thead=el("tbl").querySelector("thead"), tbody=el("tbl").querySelector("tbody");
  let cols;
  if(side==="bat"){
    cols=[["#",null],["선수","name"],["팀",null],[isCareer?"시즌":"포지션",isCareer?null:null],
      ["WAR","war"],["wRC+","wrc"],["OPS","ops"],["홈런","hr"],["oWAR","owar"],["dWAR","dwar"]];
  }else{
    cols=[["#",null],["선수","name"],["팀",null],[isCareer?"시즌":"역할",null],
      ["WAR","war"],["ERA","era"],["FIP","fip"],["이닝","ip"],["탈삼진","so"]];
  }
  thead.innerHTML="<tr>"+cols.map(c=>`<th data-k="${c[1]||''}" class="${c[1]===sortKey?'sorted':''}">${c[0]}</th>`).join("")+"</tr>";
  thead.querySelectorAll("th").forEach(th=>{const k=th.dataset.k; if(k&&(side==="bat"?SORTS_BAT:SORTS_PIT)[k]) th.onclick=()=>{sortKey=k; el("sort").value=k; render();};});

  tbody.innerHTML=recs.map((r,i)=>{
    const t=`<span class="dot" style="background:${r.color||'#888'}"></span>${r.team||(r.teams&&r.teams.join('·'))||'-'}`;
    if(side==="bat"){
      const c4=isCareer?`${r.ns}시즌`:r.pos;
      return `<tr><td class="rank">${i+1}</td><td>${r.name}</td><td style="text-align:left">${t}</td><td>${c4}</td>`
        +`<td><b>${num(r.war,2)}</b></td><td>${num(r.wrc,0)}</td><td>${num(r.ops,3)}</td><td>${r.hr}</td><td>${num(r.owar,2)}</td><td>${num(r.dwar,2)}</td></tr>`;
    }else{
      const c4=isCareer?`${r.ns}시즌`:r.role;
      return `<tr><td class="rank">${i+1}</td><td>${r.name}</td><td style="text-align:left">${t}</td><td>${c4}</td>`
        +`<td><b>${num(r.war,2)}</b></td><td>${num(r.era,2)}</td><td>${num(r.fip,2)}</td><td>${num(r.ip,1)}</td><td>${r.so}</td></tr>`;
    }
  }).join("")||`<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">해당 없음</td></tr>`;

  el("tableHint").innerHTML = isCareer
    ? `통산 ${side==="bat"?"타자":"투수"} ${sortKey.toUpperCase()} 순위 (상위 50) · <span class="warn">⚠️ 이름 기준 합산이라 동명이인이 섞일 수 있습니다(선수ID 재크롤 전).</span> wRC+/ERA 등 비율은 PA·이닝 가중평균.`
    : `${season} 시즌 ${pos==="전체"?"전체":pos} ${side==="bat"?"타자":"투수"} 순위 (상위 50). 카운팅(홈런 등)은 시즌 경기수(옛 100 vs 현 144)를 감안하세요.`;
}
</script>
</div></body></html>
"""
