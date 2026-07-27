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
        return [int(r["season"]), r.get("pno", ""), r["name"], r["team"],
                r["color"], r["pos"], float(r["war"]), float(r["owar"]),
                float(r["dwar"]), float(r["wrcplus"]), int(r["pa"]),
                int(r["hr"]), float(r["ops"])]

    def pnum(r):
        return [int(r["season"]), r.get("pno", ""), r["name"], r["team"],
                r["color"], r["role"], float(r["war"]), float(r["era"]),
                float(r["fip"]), float(r["ip"]), int(r["so"]), int(r["gs"])]

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

<div class="card" id="detailCard" style="display:none"></div>

<div class="card">
  <p class="hint" id="tableHint"></p>
  <div style="overflow-x:auto"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  <div class="legend" id="legend"></div>
</div>

<script>
const DATA = __DATA__;
// 컬럼 인덱스 (pno = 선수 고유 ID; 통산 합산 키)
const B = {season:0,pno:1,name:2,team:3,color:4,pos:5,war:6,owar:7,dwar:8,wrc:9,pa:10,hr:11,ops:12};
const P = {season:0,pno:1,name:2,team:3,color:4,role:5,war:6,era:7,fip:8,ip:9,so:10,gs:11};
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
    // JAWS = (통산 WAR + 피크) / 2, 피크 = 베스트7 시즌 WAR 합 (HOF 자격 지표)
    const wars=o.rows.map(r=>r[I.war]).sort((a,b)=>b-a);
    agg.peak=wars.slice(0,7).reduce((s,x)=>s+x,0);
    agg.jaws=(agg.war+agg.peak)/2;
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
  const summary=isBat
    ? `통산 WAR <b>${totWar.toFixed(1)}</b> · ${mine.length}시즌 · 피크 ${peak[B.season]}(WAR ${peak[B.war].toFixed(1)}) · 팀 ${teams.join('·')}`
    : `통산 WAR <b>${totWar.toFixed(1)}</b> · ${mine.length}시즌 · 피크 ${peak[P.season]}(WAR ${peak[P.war].toFixed(1)}) · 팀 ${teams.join('·')}`;
  const card=el("detailCard");
  card.innerHTML=`<div class="dhead"><span class="nm">${name}</span>
      <span class="meta">${summary}</span>
      <span class="dclose" onclick="document.getElementById('detailCard').style.display='none'">✕ 닫기</span></div>
    <div class="hint">연도별 WAR (막대에 마우스 올리면 값)</div>
    <div class="traj">${traj}</div>
    <div class="hint" style="margin-top:14px">🔍 <b>${peak[I.season]} 시즌과 닮은꼴</b> (피크 기준 최근접)</div>
    <div class="comps">${comps}</div>`;
  card.style.display="block";
  card.scrollIntoView({behavior:"smooth", block:"start"});
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
      ? {pno:r[B.pno],name:r[B.name],team:r[B.team],color:r[B.color],pos:r[B.pos],war:r[B.war],owar:r[B.owar],dwar:r[B.dwar],wrc:r[B.wrc],hr:r[B.hr],ops:r[B.ops],pa:r[B.pa]}
      : {pno:r[P.pno],name:r[P.name],team:r[P.team],color:r[P.color],role:r[P.role],war:r[P.war],era:r[P.era],fip:r[P.fip],so:r[P.so],ip:r[P.ip]});
  }
  // 정렬 (통산 모드엔 JAWS·피크 추가)
  let S=side==="bat"?{...SORTS_BAT}:{...SORTS_PIT};
  if(isCareer){ S={...S, peak:["피크7",r=>r.peak,-1], jaws:["JAWS",r=>r.jaws,-1]}; }
  if(!(sortKey in S)) sortKey="war";
  el("sort").innerHTML=Object.entries(S).map(([k,v])=>`<option value="${k}">${v[0]}</option>`).join("");
  el("sort").value=sortKey;
  const dir=S[sortKey][2];
  recs.sort((a,b)=>(a[sortKey]-b[sortKey])*dir);
  recs=recs.slice(0,50);

  // 헤더·행
  const thead=el("tbl").querySelector("thead"), tbody=el("tbl").querySelector("tbody");
  let cols;
  if(side==="bat"){
    cols=isCareer
      ? [["#",null],["선수","name"],["팀",null],["시즌",null],["통산WAR","war"],["피크7","peak"],["JAWS","jaws"],["wRC+","wrc"],["홈런","hr"]]
      : [["#",null],["선수","name"],["팀",null],["포지션",null],["WAR","war"],["wRC+","wrc"],["OPS","ops"],["홈런","hr"],["oWAR","owar"],["dWAR","dwar"]];
  }else{
    cols=isCareer
      ? [["#",null],["선수","name"],["팀",null],["시즌",null],["통산WAR","war"],["피크7","peak"],["JAWS","jaws"],["ERA","era"],["탈삼진","so"]]
      : [["#",null],["선수","name"],["팀",null],["역할",null],["WAR","war"],["ERA","era"],["FIP","fip"],["이닝","ip"],["탈삼진","so"]];
  }
  thead.innerHTML="<tr>"+cols.map(c=>`<th data-k="${c[1]||''}" class="${c[1]===sortKey?'sorted':''}">${c[0]}</th>`).join("")+"</tr>";
  thead.querySelectorAll("th").forEach(th=>{const k=th.dataset.k; if(k&&S[k]) th.onclick=()=>{sortKey=k; render();};});

  tbody.innerHTML=recs.map((r,i)=>{
    const t=`<span class="dot" style="background:${r.color||'#888'}"></span>${r.team||(r.teams&&r.teams.join('·'))||'-'}`;
    const head=`<td class="rank">${i+1}</td><td>${r.name}</td><td style="text-align:left">${t}</td>`;
    if(side==="bat"){
      return isCareer
        ? `<tr class="prow" data-pno="${r.pno||''}">${head}<td>${r.ns}시즌</td><td><b>${num(r.war,1)}</b></td><td>${num(r.peak,1)}</td><td class="jaws">${num(r.jaws,1)}</td><td>${num(r.wrc,0)}</td><td>${r.hr}</td></tr>`
        : `<tr class="prow" data-pno="${r.pno||''}">${head}<td>${r.pos}</td><td><b>${num(r.war,2)}</b></td><td>${num(r.wrc,0)}</td><td>${num(r.ops,3)}</td><td>${r.hr}</td><td>${num(r.owar,2)}</td><td>${num(r.dwar,2)}</td></tr>`;
    }else{
      return isCareer
        ? `<tr class="prow" data-pno="${r.pno||''}">${head}<td>${r.ns}시즌</td><td><b>${num(r.war,1)}</b></td><td>${num(r.peak,1)}</td><td class="jaws">${num(r.jaws,1)}</td><td>${num(r.era,2)}</td><td>${r.so}</td></tr>`
        : `<tr class="prow" data-pno="${r.pno||''}">${head}<td>${r.role}</td><td><b>${num(r.war,2)}</b></td><td>${num(r.era,2)}</td><td>${num(r.fip,2)}</td><td>${num(r.ip,1)}</td><td>${r.so}</td></tr>`;
    }
  }).join("")||`<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">해당 없음</td></tr>`;
  tbody.querySelectorAll("tr.prow").forEach(tr=>{ const pno=tr.dataset.pno;
    if(pno) tr.onclick=()=>openDetail(pno); });

  el("tableHint").innerHTML = isCareer
    ? `통산 ${side==="bat"?"타자":"투수"} ${sortKey.toUpperCase()} 순위 (상위 50) · 선수 고유 ID로 합산해 동명이인을 정확히 분리했습니다. wRC+/ERA 등 비율은 PA·이닝 가중평균.`
    : `${season} 시즌 ${pos==="전체"?"전체":pos} ${side==="bat"?"타자":"투수"} 순위 (상위 50). 카운팅(홈런 등)은 시즌 경기수(옛 100 vs 현 144)를 감안하세요.`;
}
</script>
</div></body></html>
"""
