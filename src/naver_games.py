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
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import config


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
    resp = session.get(
        config.NAVER_API_BASE,
        params=params,
        timeout=config.REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()  # HTTP 에러(4xx/5xx)면 여기서 예외 발생

    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"네이버 API 응답 실패: {body.get('code')}")

    return body["result"]["games"]


def fetch_season_games(season: int) -> list[dict]:
    """
    시즌 전체(3월~10월, 단 오늘 이후 달은 제외) 경기를 모아 리스트로 반환.
    지나간 달은 캐시를 재사용하고, 새로 받은 달은 캐시에 저장합니다.
    """
    Path(config.DATA_DIR).mkdir(exist_ok=True)  # data/ 폴더가 없으면 생성

    today = date.today()
    all_games: list[dict] = []

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

        if is_past_month and cache.exists():
            games = json.loads(cache.read_text(encoding="utf-8"))
            print(f"  [캐시] {season}년 {month}월: {len(games)}경기 (파일 재사용)")
        else:
            games = fetch_month(season, month, session)
            cache.write_text(
                json.dumps(games, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print(f"  [수집] {season}년 {month}월: {len(games)}경기")
            # 다음 요청 전에 잠깐 쉬어 서버 부담을 줄입니다
            time.sleep(config.REQUEST_DELAY_SEC)

    for month in config.SEASON_MONTHS:
        cache = _month_cache_path(season, month)
        if cache.exists():
            all_games.extend(json.loads(cache.read_text(encoding="utf-8")))

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
        resp = session.get(
            f"{config.NAVER_API_BASE}/{gid}/record",
            timeout=config.REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        info = resp.json()["result"]["recordData"].get("gameInfo", {})
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
