#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
statiz.co.kr 크롤러 (crawl4ai 0.9.x 기반)

statiz는 선수/팀 상세 통계를 로그인 뒤에 숨겨두고 Cloudflare 보호가 걸려 있어,
헤드리스 브라우저(Playwright)로 로그인 세션을 만든 뒤 렌더링된 표를 긁는다.

사용법:
  본인 계정 정보를 환경변수로 전달(코드/기록에 남지 않음):
    export STATIZ_ID='본인이메일'
    export STATIZ_PW='본인비밀번호'

  1) 먼저 구조 파악(로그인 검증 + 드롭다운/표/숨은 데이터 엔드포인트 덤프):
    python statiz_crawl.py discover

  2) 실제 수집(표 -> CSV):
    python statiz_crawl.py crawl --years 2024 2023 2022 --delay 3

주의: 본인 계정으로, 요청 간격(delay)을 두고 개인 분석 용도로만 사용할 것.
      statiz 이용약관/서버 부담을 고려해 concurrency=1, delay 기본 3초로 동작한다.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

BASE = "https://www.statiz.co.kr"
LOGIN_URL = f"{BASE}/member/?m=login"
HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
SESSION = "statiz_session"

STATIZ_ID = os.environ.get("STATIZ_ID", "")
STATIZ_PW = os.environ.get("STATIZ_PW", "")

# 로그인 벽에 걸렸을 때 statiz가 내보내는 신호들
#  - "로그인 후 이용" 문구, 또는
#  - JS alert 후 /member/?m=login 로 리다이렉트하는 얇은 셸 페이지
#  - 리다이렉트되어 로그인 폼(userPassword 입력칸)이 뜬 경우
LOGIN_WALL_SIGNS = ("로그인 후 이용", "m=login&retPage", "alert('로그인",
                    'name="userPassword"')


def is_login_wall(html: str) -> bool:
    return any(sign in html for sign in LOGIN_WALL_SIGNS)


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


_LOGGED_IN = False  # 프로세스 내 1회만 로그인하도록 가드


async def login_hook(page, context, **kwargs):
    """새 컨텍스트가 생성될 때 1회만 로그인. 이후 브라우저 컨텍스트(쿠키)가 재사용된다."""
    global _LOGGED_IN
    if _LOGGED_IN or not STATIZ_ID or not STATIZ_PW:
        if not STATIZ_ID:
            log("경고: STATIZ_ID / STATIZ_PW 환경변수가 비어 있습니다. 공개 데이터만 받게 됩니다.")
        return page
    try:
        log("로그인 페이지 접속...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)
        # 이미 로그인 상태면 로그인 폼이 없음 → 스킵
        has_form = await page.query_selector("#userID")
        if has_form is None:
            _LOGGED_IN = True
            log("이미 로그인 세션 있음. 스킵.")
            return page
        await page.fill("#userID", STATIZ_ID, timeout=8000)
        await page.fill("#userPassword", STATIZ_PW, timeout=8000)
        # 로그인 폼은 hidden iframe(ifrm_handle)로 POST(act=loginJWT) 후 JWT 쿠키 설정.
        # 폼 검증 함수 loginCheck()를 통과시키고 네이티브 submit 트리거.
        await page.evaluate(
            """() => {
                try { if (typeof loginCheck === 'function') loginCheck(); } catch(e) {}
                if (document.frm_login) document.frm_login.submit();
            }"""
        )
        await page.wait_for_timeout(4000)  # JWT 처리/쿠키 세팅 대기
        _LOGGED_IN = True
        log("로그인 완료.")
    except Exception as e:
        log("로그인 훅 오류:", repr(e))
    return page


def make_crawler():
    browser_cfg = BrowserConfig(
        headless=True,
        viewport_width=1400,
        viewport_height=1000,
        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"),
    )
    crawler = AsyncWebCrawler(config=browser_cfg)
    # 로그인 훅 등록
    crawler.crawler_strategy.set_hook("on_page_context_created", login_hook)
    return crawler


def run_cfg(capture_net=False, wait_ms=3500):
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        session_id=SESSION,
        # statiz는 광고/Cloudflare로 연결이 계속 열려 있어 networkidle 이 안 끝남.
        # domcontentloaded 로 진입 후 delay 로 JS 렌더링을 기다린다.
        wait_until="domcontentloaded",
        page_timeout=45000,
        delay_before_return_html=wait_ms / 1000.0,
        capture_network_requests=capture_net,
        verbose=False,
    )


# ── statiz 통계 폼(frm_searchSeason)의 전체 기본 파라미터 ────────────────────
# 이 폼은 GET 으로 /stats/ 에 아래 값을 전부 실어 보낸다. 필요한 것만 오버라이드한다.
#   m2  : batting(타자) / pitching(투수) / fielding(수비)
#   sy,ey: 시작/종료 연도  (단일 시즌은 sy=ey)
#   reg : 규정/누적 타석 하한.  ""(빈값)=전체 선수(최대한 많이),  C3000=통산3000타석 등
#   pr  : 한 페이지 행 수.  단일시즌 전체가 ~400명이라 1000이면 1페이지로 충분.
#         (2000 이상은 서버가 기본 10으로 리셋하므로 1000 권장)
#   so,ob: 정렬키/방향(WAR DESC)
BASE_PARAMS = {
    "m": "total", "m2": "batting", "m3": "default", "so": "WAR", "ob": "DESC",
    "sy": "1982", "ey": "2026", "te": "", "po": "", "lt": "10100", "reg": "",
    "pe": "", "ds": "", "de": "", "we": "", "hr": "", "ha": "", "ct": "", "st": "",
    "vp": "", "bo": "", "pt": "", "pp": "", "ii": "", "vc": "", "um": "", "oo": "",
    "rr": "", "sc": "", "bc": "", "ba": "", "li": "", "as": "", "ae": "", "pl": "",
    "gc": "", "lr": "", "pr": "1000", "ph": "", "hs": "", "us": "", "na": "",
    "ls": "", "sf1": "", "sk1": "", "sv1": "", "sf2": "", "sk2": "", "sv2": "", "ot": "",
}


def stat_url(**override) -> str:
    from urllib.parse import urlencode
    p = dict(BASE_PARAMS)
    p.update(override)
    return f"{BASE}/stats/?{urlencode(p)}"


def tables_from_html(html: str):
    """렌더링된 HTML에서 모든 표를 DataFrame 리스트로 추출."""
    try:
        return pd.read_html(StringIO(html))
    except ValueError:
        return []  # 표 없음


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """statiz 표 정리: 다중 헤더 평탄화 + 표 중간에 반복되는 헤더행 제거."""
    # 다중(MultiIndex) 컬럼 → 실제 스탯명(마지막 레벨)로 평탄화
    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for tup in df.columns:
            parts = [str(x) for x in tup if str(x) and not str(x).startswith("Unnamed")]
            # 정렬 화살표(▼) 같은 장식 제거하고 마지막 의미있는 이름 사용
            name = parts[-1] if parts else ""
            flat.append(name.replace("▼", "").strip())
        df.columns = flat
    else:
        df.columns = [str(c).replace("▼", "").strip() for c in df.columns]
    # statiz 는 N행마다 헤더행(Rank/Name...)을 다시 넣는다 → 첫 컬럼이 헤더값인 행 제거
    if df.shape[1] > 0:
        first = df.columns[0]
        df = df[df.iloc[:, 0].astype(str) != str(first)]
    return df.reset_index(drop=True)


def dump_controls(html: str) -> dict:
    """페이지의 select/option, 표 헤더 요약을 뽑아 구조 파악용으로 반환."""
    soup = BeautifulSoup(html, "lxml")
    selects = []
    for s in soup.find_all("select"):
        opts = [{"value": o.get("value", ""), "text": o.get_text(strip=True)}
                for o in s.find_all("option")]
        selects.append({"name": s.get("name") or s.get("id") or "?", "options": opts})
    tables = []
    for i, t in enumerate(soup.find_all("table")):
        head = t.find("tr")
        cols = [c.get_text(strip=True) for c in head.find_all(["th", "td"])] if head else []
        tables.append({"index": i, "n_cols": len(cols), "header_sample": cols[:20]})
    return {"logged_in": not is_login_wall(html), "selects": selects, "tables": tables}


async def cmd_discover(args):
    OUT.mkdir(exist_ok=True)
    ddir = OUT / "discover"
    ddir.mkdir(exist_ok=True)
    targets = {
        "main": f"{BASE}/",
        "total": f"{BASE}/stats/?m=total",
        "team": f"{BASE}/stats/?m=team",
    }
    async with make_crawler() as crawler:
        for name, url in targets.items():
            log(f"discover: {name} -> {url}")
            res = await crawler.arun(url=url, config=run_cfg(capture_net=True))
            if not res.success:
                log(f"  실패: {res.error_message}")
                continue
            html = res.html or ""
            (ddir / f"{name}.html").write_text(html, encoding="utf-8")
            info = dump_controls(html)
            # 숨은 데이터 엔드포인트 후보(네트워크 요청 중 문서 로드 이후 XHR/php/json)
            nets = []
            for r in (res.network_requests or []):
                u = r.get("url", "")
                if any(k in u for k in (".php", "ajax", "json", "data")) and "challenge-platform" not in u:
                    nets.append({"method": r.get("method"), "url": u})
            info["network_candidates"] = nets[:60]
            (ddir / f"{name}.summary.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            status = "로그인됨" if info["logged_in"] else "로그인 안됨(벽)"
            log(f"  [{status}] selects={len(info['selects'])} "
                f"tables={len(info['tables'])} net후보={len(nets)}")
            await asyncio.sleep(args.delay)
    log(f"완료. 결과: {ddir}")
    log("→ discover/*.summary.json 의 selects(연도/타자·투수 옵션)와 "
        "network_candidates(숨은 데이터 URL)를 확인해 crawl 대상 URL을 확정하세요.")


def build_targets(years, extra_urls, pr="1000"):
    """수집 대상 (라벨, URL) 목록 생성.
    연도별로 선수 타자/투수(m=total) + 팀 타자/투수(m=team)를 만든다."""
    targets = []
    for u in (extra_urls or []):
        targets.append((re.sub(r"\W+", "_", u)[-60:], u))
    for y in years:
        yy = str(y)
        # 선수 개인기록 (전체 선수: reg="")
        targets.append((f"batter_{yy}",
                        stat_url(m="total", m2="batting", sy=yy, ey=yy, reg="", pr=pr)))
        targets.append((f"pitcher_{yy}",
                        stat_url(m="total", m2="pitching", sy=yy, ey=yy, reg="", pr=pr)))
        # 팀 기록 (팀은 10개 안팎 → pr 작게)
        targets.append((f"team_batting_{yy}",
                        stat_url(m="team", m2="batting", sy=yy, ey=yy, reg="", pr="100")))
        targets.append((f"team_pitching_{yy}",
                        stat_url(m="team", m2="pitching", sy=yy, ey=yy, reg="", pr="100")))
    return targets


def expand_years(args):
    """--years 나열 또는 --from-year/--to-year 범위를 연도 리스트로."""
    if args.from_year and args.to_year:
        lo, hi = sorted((args.from_year, args.to_year))
        return list(range(hi, lo - 1, -1))  # 최신 연도부터
    return list(args.years or [])


def _num(series):
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


def regulation(team_games: int):
    """KBO 규정 기준: 규정타석 = 경기수 × 3.1, 규정이닝 = 경기수 × 1.0."""
    return round(team_games * 3.1), team_games * 1.0


def players_meta(html: str) -> list:
    """
    선수 표의 각 데이터 행에서 read_html이 버리는 정보를 '행 순서대로' 추출한다.
    반환: [{name, color, pos, pno}, ...]  (표의 데이터 행과 같은 순서·개수)

    보존하는 정보:
      · color(팀 배경색hex) — statiz 팀 약자는 KIA·KT가 둘 다 'K'로 겹치는데
        배경색은 팀·프랜차이즈별로 고유. 색으로 구분(다운스트림 매핑).
        구조: <div class="teams"><span bg>26+</span><span bg>K</span><span>3B</span></div>
      · pos(포지션) — 위 색 없는 마지막 span.
      · pno(선수 고유 ID) — 이름 링크 href의 p_no=... 값. 동명이인이 시즌 안에도
        있어(2026 타자만 7쌍) 이름 합산은 위험 → 통산 집계는 pno로 해야 안전.

    [이름이 아니라 행 순서로 뽑는 이유]
      동명이인을 이름으로 키하면 서로 뭉갠다. read_html의 clean_table가 반복
      헤더행(첫 셀이 헤더명)을 제거하므로, 여기서도 동일 규칙으로 걸러 순서를
      맞춘다. biggest_table에서 개수가 df와 같을 때 위치로 대입한다.
    """
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        if t.select("div.teams"):
            table = t
            break
    if table is None:
        return []
    header_first = ""
    head = table.find("tr")
    if head:
        hc = head.find_all(["th", "td"])
        header_first = hc[0].get_text(strip=True) if hc else ""

    out = []
    for row in table.find_all("tr"):
        div = row.find("div", class_="teams")
        if not div:                       # 헤더/구분 행 등 팀칸 없는 행 제외
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        if header_first and cells[0].get_text(strip=True) == header_first:
            continue                      # 반복 헤더행 제외 (clean_table와 동일)
        # 이름·선수ID
        link = cells[1].find("a")
        pno = ""
        if link:
            m = re.search(r"p_no=(\d+)", link.get("href", ""))
            pno = m.group(1) if m else ""
        name = cells[1].get_text(strip=True)
        # 팀색·포지션
        spans = div.find_all("span")
        colored = [s for s in spans if "background" in (s.get("style") or "")]
        plain = [s for s in spans if "background" not in (s.get("style") or "")]
        color = ""
        if colored:
            m = re.search(r"background:\s*(#[0-9a-fA-F]{3,6})",
                          colored[-1].get("style", ""))
            color = m.group(1).lower() if m else ""
        pos = plain[-1].get_text(strip=True) if plain else ""
        out.append({"name": name, "color": color, "pos": pos, "pno": pno})
    return out


def biggest_table(html: str):
    dfs = [clean_table(d) for d in tables_from_html(html)]
    dfs = [d for d in dfs if d.shape[0] > 0]
    if not dfs:
        return pd.DataFrame()
    df = max(dfs, key=lambda d: d.shape[0] * d.shape[1])
    # 선수 표라면 TeamColor·Pos·PlayerNo(선수ID)를 보존한다. read_html이 버리는
    # 정보. 행 개수가 맞으면 '위치'로 대입(동명이인 안전), 아니면 이름-키 폴백.
    if "Name" in df.columns and "Team" in df.columns:
        meta = players_meta(html)
        if len(meta) == len(df):
            df["TeamColor"] = [m["color"] for m in meta]
            df["Pos"] = [m["pos"] for m in meta]
            df["PlayerNo"] = [m["pno"] for m in meta]
        elif meta:                        # 개수 불일치 → 이름-키 폴백(정확도↓)
            byname = {m["name"]: m for m in meta}
            df["TeamColor"] = df["Name"].map(lambda n: byname.get(n, {}).get("color", ""))
            df["Pos"] = df["Name"].map(lambda n: byname.get(n, {}).get("pos", ""))
            df["PlayerNo"] = df["Name"].map(lambda n: byname.get(n, {}).get("pno", ""))
    return df


async def cmd_crawl(args):
    OUT.mkdir(exist_ok=True)
    csvdir = OUT / "csv"
    csvdir.mkdir(exist_ok=True)
    years = expand_years(args)
    if not years and not args.url:
        log("수집 대상이 없습니다. --years/--from-year·--to-year 또는 --url 로 지정하세요.")
        return
    pr = str(args.pr)
    qualified = not args.all_players  # 기본: 규정타석/이닝 이상만
    sfx = f"_{args.tag}" if args.tag else ""  # 파일명 접미사(규정본과 분리 저장용)
    log(f"연도 {len(years)}개, 모드={'규정타석 이상' if qualified else '전체 선수'}"
        f"{f', 태그={args.tag}' if args.tag else ''}, delay={args.delay}s")
    manifest = []

    async with make_crawler() as crawler:
        async def fetch(label, url):
            # Cloudflare 403(anti-bot)·일시 실패 대비 백오프 재시도
            for attempt in range(1, args.retries + 2):
                log(f"crawl: {label}" + (f" (재시도 {attempt-1})" if attempt > 1 else ""))
                res = await crawler.arun(url=url, config=run_cfg())
                await asyncio.sleep(args.delay)
                if res.success and not is_login_wall(res.html or ""):
                    df = biggest_table(res.html or "")
                    if df.shape[0] > 0:
                        return df
                    log("  0행")
                else:
                    log(f"  실패: {(res.error_message or '로그인 벽')[:70]}")
                if attempt <= args.retries:
                    wait = args.retry_wait * attempt  # 20s, 40s, ...
                    log(f"  {wait:.0f}초 대기 후 재시도...")
                    await asyncio.sleep(wait)
            return pd.DataFrame()

        def save(df, label):
            if df.shape[0] == 0:
                log("  표 없음(0행)"); return
            fn = csvdir / f"{label}.csv"
            df.to_csv(fn, index=False, encoding="utf-8-sig")
            log(f"  저장: {fn.name} ({df.shape[0]}행 {df.shape[1]}열)")
            manifest.append({"file": fn.name, "rows": int(df.shape[0]), "cols": int(df.shape[1])})

        # 임의 URL 직접 지정분
        for u in (args.url or []):
            save(await fetch(re.sub(r"\W+", "_", u)[-50:], u), re.sub(r"\W+", "_", u)[-50:])

        for y in years:
            yy = str(y)
            # 재개: 이미 받은 연도는 건너뜀.
            #  - TeamColor 컬럼이 있어야 '패치 반영된 최신본'으로 간주(옛 파일은 재크롤).
            if args.skip_existing:
                bpath = csvdir / f"batter_{yy}{sfx}.csv"
                ppath = csvdir / f"pitcher_{yy}{sfx}.csv"
                def done(p):
                    try:
                        df = pd.read_csv(p, nrows=1)
                        return len(df.columns) > 0 and "PlayerNo" in df.columns
                    except Exception:
                        return False
                if bpath.exists() and ppath.exists() and done(bpath) and done(ppath):
                    log(f"skip: {yy} (패치 반영본 이미 있음)")
                    continue
            # 1) 팀 표 먼저 → 경기수(teamG) 산출.
            #    전체모드(--all-players)는 규정 필터가 없고 팀표는 규정본에서 이미 받았으므로
            #    요청 절약을 위해 팀표를 건너뛴다.
            team_games = 0
            if qualified:
                tb = await fetch(f"team_batting_{yy}",
                                 stat_url(m="team", m2="batting", sy=yy, ey=yy, reg="", pr="100"))
                save(tb, f"team_batting_{yy}{sfx}")
                tp = await fetch(f"team_pitching_{yy}",
                                 stat_url(m="team", m2="pitching", sy=yy, ey=yy, reg="", pr="100"))
                save(tp, f"team_pitching_{yy}{sfx}")
                team_games = int(_num(tb["G"]).max()) if ("G" in tb.columns and tb.shape[0]) else 0
            regPA, regIP = regulation(team_games) if team_games else (0, 0)

            # 2) 선수 표(전체) → 규정 필터(옵션)
            bat = await fetch(f"batter_{yy}",
                              stat_url(m="total", m2="batting", sy=yy, ey=yy, reg="", pr=pr))
            if qualified and team_games and "PA" in bat.columns:
                bat = bat[_num(bat["PA"]) >= regPA].reset_index(drop=True)
                log(f"  규정타석 {regPA}(경기 {team_games}) 이상 → {bat.shape[0]}명")
            save(bat, f"batter_{yy}{sfx}")

            pit = await fetch(f"pitcher_{yy}",
                              stat_url(m="total", m2="pitching", sy=yy, ey=yy, reg="", pr=pr))
            if qualified and team_games and "IP" in pit.columns:
                pit = pit[_num(pit["IP"]) >= regIP].reset_index(drop=True)
                log(f"  규정이닝 {regIP:g} 이상 → {pit.shape[0]}명")
            save(pit, f"pitcher_{yy}{sfx}")

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"완료. CSV: {csvdir} / 매니페스트: {OUT/'manifest.json'}")


def main():
    p = argparse.ArgumentParser(description="statiz.co.kr crawler (crawl4ai)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="로그인 검증 + 구조/숨은 엔드포인트 덤프")
    d.add_argument("--delay", type=float, default=3.0)
    d.set_defaults(func=cmd_discover)

    c = sub.add_parser("crawl", help="표 -> CSV 수집")
    c.add_argument("--years", type=int, nargs="*", default=[datetime.now().year],
                   help="수집할 연도 나열 (예: --years 2024 2023)")
    c.add_argument("--from-year", type=int, help="범위 시작 연도 (--to-year 와 함께)")
    c.add_argument("--to-year", type=int, help="범위 종료 연도")
    c.add_argument("--pr", type=int, default=1000, help="선수표 최대 행수(기본 1000)")
    c.add_argument("--all-players", action="store_true",
                   help="규정타석/이닝 필터 없이 전체 선수 수집(기본은 규정 이상만)")
    c.add_argument("--skip-existing", action="store_true",
                   help="이미 받은 연도는 건너뜀(차단 후 이어받기용)")
    c.add_argument("--tag", default="",
                   help="파일명 접미사(예: --all-players --tag all → batter_2024_all.csv)")
    c.add_argument("--retries", type=int, default=2, help="실패/403 시 재시도 횟수")
    c.add_argument("--retry-wait", type=float, default=20.0, help="재시도 대기 기준초(점증)")
    c.add_argument("--url", nargs="*", help="직접 지정할 수집 URL들")
    c.add_argument("--delay", type=float, default=6.0,
                   help="요청 간 간격초(기본 6, Cloudflare 차단 방지)")
    c.set_defaults(func=cmd_crawl)

    args = p.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    if not STATIZ_ID:
        log("힌트: 로그인이 필요한 데이터는 STATIZ_ID/STATIZ_PW 환경변수를 먼저 설정하세요.")
    main()
