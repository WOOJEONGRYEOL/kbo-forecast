# -*- coding: utf-8 -*-
"""
naver_games.py — 경기 결과 수집기 (네이버 스포츠 API)
======================================================

[역할]
  네이버 스포츠의 공개 JSON API에서 KBO 정규시즌 경기 결과를
  달 단위로 받아와, "팀별 경기 로그" (팀 입장에서 본 득점/실점/승패)
  형태의 pandas DataFrame으로 가공합니다.

[왜 네이버인가?]
  - 스탯티즈 일정 페이지는 2026년 현재 로그인이 필요해졌고,
  - KBO 공식 홈페이지의 Schedule.asmx 엔드포인트는 막혀 있습니다.
  - 네이버 API는 인증 없이 깔끔한 JSON을 주므로 가장 안정적입니다.

[캐시 전략]
  이미 지나간 달의 경기 결과는 바뀌지 않으므로 data/ 폴더에
  JSON으로 저장해 두고, 다음 실행부터는 파일을 재사용합니다.
  → 서버에 불필요한 요청을 반복하지 않는 크롤링 예절이기도 합니다.
  (진행 중인 달은 매번 새로 받아옵니다)
"""

import calendar
import json
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

import config


# ──────────────────────────────────────────────────────────────────────────
# 네이버 API GET 헬퍼 — curl(--http1.1) 우선, requests 폴백
# ──────────────────────────────────────────────────────────────────────────
# 2026-10 하순부터 네이버 WAF가 '파이썬 requests의 TLS 지문 + HTTP/2'를 조용히
#   드롭하기 시작했다(TCP·TLS는 붙는데 응답 바이트가 안 와 ReadTimeout). 그 결과
#   로컬·GitHub Actions 양쪽 모두 10월 라이브 수집이 며칠째 실패→캐시로 폴백하며
#   포스트시즌 결과가 Oct-26 스냅샷에 굳어버렸다. 같은 요청을 'curl --http1.1 +
#   브라우저 헤더'로 보내면 0.06초 만에 200이 온다(지문·프로토콜 우회). 그래서
#   수집은 curl을 먼저 쓰고, curl이 없거나 실패하면 requests로 폴백한다.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36")
_CURL = shutil.which("curl")

# '진행 중인 달'(오늘이 속한 달)을 이번 실행에서 라이브로 새로 받았는가?
#   True  = 신선한 데이터로 빌드됨 → 라이브 페이지(가을야구/오늘/성적표) 커밋 안전
#   False = 네이버 수집 실패로 캐시 폴백 → stale. 이때 라이브 페이지를 재생성·커밋하면
#           더 신선한 커밋(국내 IP 로컬 빌더가 올린 것)을 '빈/과거' 상태로 덮어써 퇴행한다.
#   네이버가 GitHub(해외·클라우드) IP를 드롭해 GH Actions는 항상 False가 된다. 빌드
#   스크립트는 이 값이 False면 라이브 페이지 생성을 건너뛴다(퇴행 방지).
LIVE_MONTH_FETCH_OK = True


def _get_json(url: str, params: dict | None = None, timeout: int | None = None) -> dict:
    """네이버 공개 API를 GET해 JSON(dict)으로 반환. 네트워크/파싱 실패는 예외로 올린다
    (상위의 캐시 폴백이 받도록). curl 우회를 먼저 시도하고 안 되면 requests를 쓴다."""
    timeout = timeout or config.REQUEST_TIMEOUT_SEC
    full = url + (("?" + urlencode(params)) if params else "")
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://m.sports.naver.com/",
    }
    if _CURL:
        cmd = [_CURL, "--http1.1", "-sS", "--fail", "-m", str(timeout)]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(full)
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
            if out.returncode == 0 and out.stdout:
                return json.loads(out.stdout)
        except Exception:
            pass   # 아래 requests 폴백으로 넘어간다
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _month_cache_path(season: int, month: int) -> Path:
    """해당 시즌·달의 캐시 파일 경로를 돌려줍니다. 예: data/games_2026_06.json"""
    return Path(config.DATA_DIR) / f"games_{season}_{month:02d}.json"


def fetch_month(season: int, month: int, session: requests.Session) -> list[dict]:
    """
    한 달치 KBO 경기 목록을 네이버 API에서 받아옵니다.

    반환값: 경기 dict의 리스트 (네이버 API 원본 형식 그대로)
            각 dict에는 gameDate, homeTeamCode, homeTeamScore,
            awayTeamCode, awayTeamScore, statusCode(RESULT/BEFORE),
            cancel(우천취소 여부) 등이 들어 있습니다.
    """
    # 달의 첫날 ~ 마지막날 범위를 만듭니다.
    # 주의: 존재하지 않는 날짜(예: 4월 31일)를 보내면 API가 400 에러를
    # 돌려주므로, calendar.monthrange로 그 달의 실제 마지막 날을 구합니다.
    last_day = calendar.monthrange(season, month)[1]
    from_date = f"{season}-{month:02d}-01"
    to_date = f"{season}-{month:02d}-{last_day:02d}"

    params = {
        # basic: 팀/점수, statusInfo: 경기 진행 상태 — 필요한 필드만 요청
        "fields": "basic,stadium,statusInfo",
        "upperCategoryId": "kbaseball",  # 야구 대분류
        "categoryId": "kbo",             # KBO 리그 (퓨처스는 kbominor)
        "fromDate": from_date,
        "toDate": to_date,
        "size": 500,  # 한 달 최대 경기 수(약 125)보다 넉넉하게
    }
    body = _get_json(config.NAVER_API_BASE, params)  # curl 우선, requests 폴백
    if not body.get("success"):
        raise RuntimeError(f"네이버 API 응답 실패: {body.get('code')}")

    return body["result"]["games"]


def _has_stale_pending(games: list, today_iso: str) -> bool:
    """과거 날짜인데 아직 미결(BEFORE/READY 등)인 경기가 있나?
    우천 순연 경기가 보충일에 치러졌는데도 캐시엔 BEFORE로 굳어 결과를 놓치는
    상황을 잡는다. 이런 달은 캐시를 믿지 말고 재조회해 보충 결과를 반영한다."""
    FINAL = {"RESULT", "ENDED", "CANCEL"}
    for g in games:
        if (g.get("gameDate", "") < today_iso and not g.get("cancel")
                and g.get("statusCode") not in FINAL):
            return True
    return False


def fetch_season_games(season: int, repair_stale: bool = False) -> list[dict]:
    """
    시즌 전체(3월~10월, 단 오늘 이후 달은 제외) 경기를 모아 리스트로 반환.
    지나간 달은 캐시를 재사용하고, 새로 받은 달은 캐시에 저장합니다.

    repair_stale=True면, 지난 달 캐시라도 '과거인데 미결(BEFORE)' 경기가 남아 있으면
    재조회한다 — 우천 순연 경기의 보충 결과가 캐시에 반영 안 돼 승패/순위가 틀어지는
    버그 방지(일일 빌드 등 정확도 중요한 경로에서 사용). 잦은 today 빌드는 기본 False.
    """
    Path(config.DATA_DIR).mkdir(exist_ok=True)  # data/ 폴더가 없으면 생성

    global LIVE_MONTH_FETCH_OK
    today = date.today()
    today_iso = today.isoformat()
    all_games: list[dict] = []
    live_ok = True   # 진행 중인 달을 라이브로 받았는지(실패 폴백이면 False로)

    # 네이버 API에 브라우저인 척하는 UA를 달아줍니다
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})

    for month in config.SEASON_MONTHS:
        # 미래의 달은 경기가 없으니 건너뜁니다
        if season == today.year and month > today.month:
            break

        cache = _month_cache_path(season, month)

        # '완전히 지나간 달'만 캐시를 신뢰합니다.
        # 이번 달은 매일 경기가 추가되므로 항상 새로 받아옵니다.
        is_past_month = (season < today.year) or (month < today.month)

        trust_cache = is_past_month and cache.exists()
        if trust_cache and repair_stale:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if _has_stale_pending(cached, today_iso):
                trust_cache = False          # 과거 미결 경기 → 보충 결과 반영 위해 재조회

        if trust_cache:
            games = json.loads(cache.read_text(encoding="utf-8"))
            print(f"  [캐시] {season}년 {month}월: {len(games)}경기 (파일 재사용)")
        else:
            # 현재 달 라이브 수집. 실패(네트워크 타임아웃 등) 시 이전 캐시로 폴백한다.
            #   폴백 안 하면 예외가 fetch_season_games 전체를 죽이고, resolve_season이
            #   이를 '경기 없음'으로 오인해 시즌을 작년으로 되돌리는 치명 버그가 난다.
            try:
                games = fetch_month(season, month, session)
                cache.write_text(
                    json.dumps(games, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  [수집] {season}년 {month}월: {len(games)}경기")
                time.sleep(config.REQUEST_DELAY_SEC)
            except Exception as e:
                if not is_past_month:
                    live_ok = False      # 진행 중인 달 라이브 수집 실패 → stale 플래그
                if cache.exists():
                    games = json.loads(cache.read_text(encoding="utf-8"))
                    print(f"  [경고] {season}년 {month}월 수집 실패({type(e).__name__}) → 캐시 재사용 {len(games)}경기")
                else:
                    print(f"  [경고] {season}년 {month}월 수집 실패({type(e).__name__}) · 캐시 없음 → 건너뜀")
                    games = []

    for month in config.SEASON_MONTHS:
        cache = _month_cache_path(season, month)
        if cache.exists():
            all_games.extend(json.loads(cache.read_text(encoding="utf-8")))

    LIVE_MONTH_FETCH_OK = live_ok
    return all_games


def filter_regular_season(games: list[dict]) -> list[dict]:
    """
    시범경기를 걸러내고 정규시즌 경기만 남깁니다.

    [왜 필요한가?]
      네이버 일정 API(categoryId=kbo)는 3월의 시범경기까지 함께
      돌려주는데, 일정 응답에는 구분 필드가 없습니다.
      대신 경기별 상세(record) API의 gameInfo.gameFlag가
      시범경기=1 / 정규시즌=0 으로 구분해 줍니다.

    [비용 절약]
      시범경기는 3월(개막 전)에만 열리므로, 3월 경기만
      상세 API로 확인하고 결과를 data/gameflags_시즌.json 에
      캐시합니다. 4월 이후 경기는 확인 없이 통과시킵니다.
    """
    # 0) 포스트시즌·올스타 등 '날짜로 시작하지 않는' gameId 제외 (항상 적용).
    #    정규시즌 gameId는 YYYYMMDD로 시작하지만, 144경기 이후 가을야구는 4444…(올스타는
    #    9999…)처럼 시리즈 코드로 시작한다. 이걸 거르지 않으면 포스트시즌이 정규 집계·순위·
    #    잔여경기에 섞여 팀 경기수가 144를 넘는다.
    def _is_regular_id(g):
        gid = str(g.get("gameId", "")); d = str(g.get("gameDate", "")).replace("-", "")
        return len(gid) >= 8 and len(d) == 8 and gid[:8] == d
    post = [g for g in games if not _is_regular_id(g)]
    if post:
        games = [g for g in games if _is_regular_id(g)]
        print(f"  → 포스트시즌·특별경기 {len(post)}건 제외 (gameId 날짜 접두 불일치)")

    march_games = [g for g in games if int(g["gameDate"][5:7]) <= 3]
    if not march_games:
        return games

    season = march_games[0]["gameDate"][:4]
    cache = Path(config.DATA_DIR) / f"gameflags_{season}.json"
    flags: dict = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT,
                            "Accept": "application/json"})

    new = 0
    for g in march_games:
        gid = g["gameId"]
        if gid in flags:
            continue
        # 아직 안 열린 경기는 record가 없으므로 임시로 정규시즌 취급
        if g.get("statusCode") != "RESULT":
            continue
        info = _get_json(f"{config.NAVER_API_BASE}/{gid}/record"
                         )["result"]["recordData"].get("gameInfo", {})
        flags[gid] = str(info.get("gameFlag", "0"))
        new += 1
        time.sleep(0.3)

    if new:
        cache.write_text(json.dumps(flags, indent=1), encoding="utf-8")
        print(f"  [확인] 3월 경기 {new}건의 시범/정규 구분을 새로 조회")

    regular = [g for g in games if flags.get(g["gameId"], "0") == "0"]
    dropped = len(games) - len(regular)
    if dropped:
        print(f"  → 시범경기 {dropped}건 제외 (정규시즌 {len(regular)}건 사용)")
    return regular


def resolve_season(verbose: bool = True) -> int:
    """실제로 반영할 시즌을 감지한다(시범경기와 혼동 없이).

    규칙: 환경변수 KBO_SEASON이 있으면 그 값. 없으면 올해부터 '정규시즌에서
      실제 치러진(RESULT/ENDED) 경기가 있는 최신 연도'를 고른다.
        · 시범경기는 filter_regular_season이 제외 → 개막 전엔 안 넘어감.
        · '치러진' 경기를 요구 → 미개막 스케줄(BEFORE)만 있으면 직전 시즌 유지.
        · 정규 개막 첫 경기가 나오는 순간 새 시즌으로 전환.
    """
    import os
    import datetime
    env = os.environ.get("KBO_SEASON")
    if env:
        return int(env)
    year = datetime.date.today().year
    for cand in (year, year - 1):
        try:
            reg = filter_regular_season(fetch_season_games(cand))
        except Exception:
            continue
        if any(g.get("statusCode") in ("RESULT", "ENDED") for g in reg):
            if verbose and cand != config.SEASON:
                print(f"  → 시즌 자동감지: {cand} (정규 치러진 경기 확인)")
            return cand
    return year - 1


def filter_official_teams(games: list[dict]) -> list[dict]:
    """
    정규 10개 구단이 아닌 팀이 낀 경기를 제거합니다.

    [왜 필요한가?]
      7월 올스타 브레이크 기간에 네이버 KBO 일정(categoryId=kbo)에는
      '올스타전'이 함께 들어옵니다. 이 경기의 두 팀(나눔/드림 올스타)은
      정규 10개 구단 코드가 아니어서, 그대로 두면 팀당 1경기짜리
      '유령 팀' 2개가 만들어져 대시보드 차트와 박스스코어 집계를
      오염시키고 파이프라인을 멈추게 합니다.
      config.TEAM_NAMES(공식 10개 팀 코드)에 없는 팀이 낀 경기는 버립니다.
    """
    official = set(config.TEAM_NAMES)
    kept = [g for g in games
            if g.get("homeTeamCode") in official
            and g.get("awayTeamCode") in official]
    dropped = len(games) - len(kept)
    if dropped:
        print(f"  → 비(非)정규구단 경기 {dropped}건 제외 (올스타전 등)")
    return kept


def build_team_game_log(games: list[dict]) -> pd.DataFrame:
    """
    네이버 원본 경기 목록 → "팀 관점의 경기 로그"로 변환합니다.

    경기 하나는 두 팀의 입장이 각각 존재하므로, 경기 1건당
    행이 2개(홈팀 관점 + 원정팀 관점) 생깁니다.

    반환 DataFrame 컬럼:
      date        경기 날짜 (YYYY-MM-DD)
      team        팀 코드 (LG, OB, ...)
      opponent    상대 팀 코드
      runs_for    이 팀이 낸 득점
      runs_against이 팀이 내준 실점
      result      'W'(승) / 'L'(패) / 'D'(무승부)

    ※ 아직 안 열린 경기(BEFORE), 우천취소(cancel=True),
      서스펜디드 미완료 경기는 제외합니다.
    """
    rows = []
    for g in games:
        # 결과가 확정된 경기만 사용합니다
        if g.get("statusCode") != "RESULT" or g.get("cancel"):
            continue

        home_score = g.get("homeTeamScore")
        away_score = g.get("awayTeamScore")
        if home_score is None or away_score is None:
            continue  # 점수가 비어 있으면 스킵 (방어적 처리)

        def result_of(my: int, opp: int) -> str:
            """득점 비교로 승/패/무를 판정합니다.
            KBO는 연장 12회까지 동점이면 무승부가 실제로 존재합니다!"""
            if my > opp:
                return "W"
            if my < opp:
                return "L"
            return "D"

        # 홈팀 관점의 행
        rows.append({
            "date": g["gameDate"],
            "team": g["homeTeamCode"],
            "opponent": g["awayTeamCode"],
            "runs_for": home_score,
            "runs_against": away_score,
            "result": result_of(home_score, away_score),
        })
        # 원정팀 관점의 행
        rows.append({
            "date": g["gameDate"],
            "team": g["awayTeamCode"],
            "opponent": g["homeTeamCode"],
            "runs_for": away_score,
            "runs_against": home_score,
            "result": result_of(away_score, home_score),
        })

    df = pd.DataFrame(rows)
    # 같은 날 더블헤더가 있어도 순서만 맞으면 rolling 계산에 문제없습니다.
    # 팀별 → 날짜순으로 정렬해 둡니다.
    df = df.sort_values(["team", "date"]).reset_index(drop=True)
    return df
