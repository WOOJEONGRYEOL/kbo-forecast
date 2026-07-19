# -*- coding: utf-8 -*-
"""
boxscore.py — 경기별 박스스코어 수집기 (투수 기록)
====================================================

[역할]
  네이버 스포츠의 경기 상세 API에서 경기마다 등판한 투수들의
  기록(이닝, 자책점, 피홈런, 볼넷, 탈삼진 등)을 받아와,
  시즌 전체를 합산한 '투수별 시즌 성적표'(ERA, FIP 포함)를 만듭니다.

[왜 박스스코어를 합산하나?]
  - KBO 공식 기록 페이지는 크롤러의 POST 요청을 차단하고,
  - 스탯티즈는 로그인제, kbostuff에는 투수 '결과' 지표가 없습니다.
  - 대신 네이버 경기별 박스스코어를 전부 더하면 어떤 투수든
    정확한 시즌 누적 기록을 직접 만들 수 있습니다.
  - 보너스: 경기 단위 데이터라서 나중에 '최근 10경기 rolling FIP'
    같은 고도화도 이 모듈 위에서 바로 가능합니다.

[핵심 발견]
  박스스코어의 선수 코드(pcode)가 kbostuff.app의 pitcher_pcode와
  같은 체계(스포츠투아이 공식 코드)입니다. 덕분에 두 데이터를
  이름이 아니라 코드로 정확하게 조인할 수 있습니다.

[캐시 전략]
  끝난 경기의 박스스코어는 영원히 바뀌지 않으므로
  data/box/경기ID.json 으로 저장하고 재사용합니다.
  → 첫 실행만 느리고(경기당 1회 요청), 이후에는 즉시 로드됩니다.
"""

import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

import config

# 경기별 박스스코어 API (schedule/games 뒤에 /record를 붙이면 됩니다)
RECORD_URL = config.NAVER_API_BASE + "/{game_id}/record"

# 박스스코어는 경기 수가 많아(시즌 700+경기) 요청 간격을 짧게 잡되,
# 그래도 서버를 배려해 최소한의 간격은 둡니다.
BOX_DELAY_SEC = 0.3


def _cache_path(game_id: str) -> Path:
    return Path(config.DATA_DIR) / "box" / f"{game_id}.json"


def fetch_game_pitchers(game_id: str, session: requests.Session) -> list[dict]:
    """
    한 경기의 투수 기록(홈+원정)을 리스트로 반환합니다.
    캐시가 있으면 캐시를, 없으면 API에서 받아 캐시에 저장합니다.
    """
    cache = _cache_path(game_id)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    resp = session.get(
        RECORD_URL.format(game_id=game_id),
        timeout=config.REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    body = resp.json()

    record = body["result"]["recordData"]
    game_info = record.get("gameInfo", {})
    box = record.get("pitchersBoxscore", {})

    rows = []
    # 'home'/'away' 각각의 투수 명단을 팀 코드와 함께 평평하게 폅니다
    for side, team_key in (("home", "hCode"), ("away", "aCode")):
        team_code = game_info.get(team_key, "")
        for p in box.get(side, []):
            rows.append({
                "game_id": game_id,
                "team": team_code,
                "pcode": str(p.get("pcode", "")),
                "name": p.get("name", ""),
                "inn": p.get("inn", "0"),      # "5 2/3" 같은 문자열
                "er": p.get("er", 0),           # 자책점
                "r": p.get("r", 0),             # 실점
                "hr": p.get("hr", 0),           # 피홈런
                "bb": p.get("bb", 0),           # 볼넷
                "bbhp": p.get("bbhp", 0),       # 볼넷+사구 합계
                "kk": p.get("kk", 0),           # 탈삼진
                "bf": p.get("bf", 0),           # 상대한 타자 수
                "hit": p.get("hit", 0),         # 피안타
            })

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    time.sleep(BOX_DELAY_SEC)  # 새로 받아온 경우에만 잠깐 쉬기
    return rows


# 네이버 박스스코어는 부분 이닝을 유니코드 분수 문자(⅓, ⅔)로 표기합니다.
# 예: "5 ⅔" = 5이닝 + 2아웃.  ASCII 분수("2/3")도 만약을 위해 지원합니다.
_FRACTION_OUTS = {"⅓": 1, "⅔": 2, "1/3": 1, "2/3": 2}


def _innings_to_outs(inn_str) -> int:
    """
    "5 ⅔" 같은 이닝 문자열을 아웃카운트(정수)로 바꿉니다.
    이닝을 소수(5.67)로 다루면 합산할 때 오차가 쌓이므로,
    세이버메트릭스에서는 항상 아웃카운트로 계산하는 것이 정석입니다.

    지원 표기:
      - "5 ⅔" / "⅔" / "5"        공백으로 분리된 정수+유니코드 분수
      - "5⅔"                    공백 없이 붙은 형태 (자동 분리)
      - "6.1" / "6.2"              KBO 소수 이닝 표기(.1=⅓, .2=⅔)
      - "" / None / "0"            0아웃

    ⚠️ 이전 구현은 알 수 없는 표기에서 ValueError를 던져
      경기 하나만 이상해도 파이프라인 전체가 멈췄습니다.
      이제는 경고만 남기고 그 값을 0아웃으로 건너뛰어,
      매일 도는 자동 갱신이 한 경기 때문에 죽지 않게 합니다.
    """
    if inn_str is None:
        return 0
    s = str(inn_str).strip()
    if not s:
        return 0

    # KBO 소수 이닝 표기: 6.1 = 6⅓, 6.2 = 6⅔ (드물게 API가 이 형식을 줌)
    m = re.fullmatch(r"(\d+)\.([012])", s)
    if m:
        return int(m.group(1)) * 3 + int(m.group(2))

    # 정수와 유니코드 분수가 공백 없이 붙은 형태를 분리: "5⅔" -> "5 ⅔"
    s = re.sub(r"(?<=\d)(?=[⅓⅔])", " ", s)

    outs = 0
    for part in s.split():
        if part in _FRACTION_OUTS:          # 분수 부분: "⅔" -> 2아웃
            outs += _FRACTION_OUTS[part]
        elif part.isdigit():                # 정수 부분: "5" -> 15아웃
            outs += int(part) * 3
        else:
            # 알 수 없는 표기는 버리되, 경기 전체를 죽이지 않습니다
            print(f"  [경고] 해석할 수 없는 이닝 표기 무시: {inn_str!r}",
                  file=sys.stderr)
    return outs


def collect_season_pitching(games: list[dict]) -> pd.DataFrame:
    """
    완료된 경기 전체의 투수 박스스코어를 모아
    '경기 단위' DataFrame으로 반환합니다. (한 행 = 한 투수의 한 경기)

    입력: naver_games.fetch_season_games()의 결과 (경기 목록)
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    })

    done = [g for g in games
            if g.get("statusCode") == "RESULT" and not g.get("cancel")]

    all_rows: list[dict] = []
    new_fetch = 0
    for i, g in enumerate(done):
        cached = _cache_path(g["gameId"]).exists()
        rows = fetch_game_pitchers(g["gameId"], session)
        # 경기 날짜를 행마다 붙여줍니다 (나중에 rolling 분석용)
        for r in rows:
            r["date"] = g["gameDate"]
        all_rows.extend(rows)
        if not cached:
            new_fetch += 1
            # 새로 받는 경기가 많을 때 진행 상황을 보여줍니다
            if new_fetch % 50 == 0:
                print(f"    ... 박스스코어 신규 수집 {new_fetch}경기 "
                      f"(전체 {i + 1}/{len(done)})")

    print(f"  → 박스스코어 {len(done)}경기 (신규 {new_fetch}, "
          f"캐시 {len(done) - new_fetch})")
    return pd.DataFrame(all_rows)


def identify_rotation(box: pd.DataFrame, min_starts: int = 3) -> pd.DataFrame:
    """
    박스스코어에서 팀별 '선발 로테이션'을 식별합니다.

    [원리] 박스스코어의 투수 명단은 등판 순서대로 나열되므로,
      각 경기·팀의 '첫 번째 투수' = 그 경기 선발입니다.
      시즌 내내 선발 등판이 잦은 투수(min_starts회 이상)를 로테이션으로 봅니다.

    반환: pcode, team, name, starts (선발 등판 수), 팀·등판수 내림차순
    """
    # game_id×team별 첫 행 = 선발 (box는 등판 순서를 보존)
    starters = box.drop_duplicates(["game_id", "team"], keep="first")
    counts = (starters.groupby(["pcode", "team", "name"])
              .size().reset_index(name="starts"))
    counts = counts[counts["starts"] >= min_starts]
    return counts.sort_values(["team", "starts"], ascending=[True, False])


def season_pitcher_stats(box: pd.DataFrame) -> pd.DataFrame:
    """
    경기 단위 기록을 투수별 시즌 성적으로 합산하고 ERA/FIP를 계산합니다.

    FIP(수비 무관 평균자책점)란?
      투수가 온전히 책임지는 사건(홈런, 볼넷, 사구, 삼진)만으로
      방어율 스케일의 성적을 재구성한 지표입니다.
        FIP = (13×피홈런 + 3×(볼넷+사구) − 2×탈삼진) / 이닝 + 상수
      상수는 '리그 FIP 평균 = 리그 ERA 평균'이 되도록 매 시즌 맞춥니다.
      ERA가 FIP보다 한참 높다면? → 수비 도움을 못 받았거나 불운.
      ERA가 FIP보다 한참 낮다면? → 수비/운의 덕을 본 것 (지속 어려움).
    """
    grouped = box.groupby(["pcode", "name"]).agg(
        games=("game_id", "nunique"),
        outs=("inn", lambda s: sum(_innings_to_outs(x) for x in s)),
        er=("er", "sum"),
        hr=("hr", "sum"),
        bb=("bb", "sum"),
        bbhp=("bbhp", "sum"),
        kk=("kk", "sum"),
        bf=("bf", "sum"),
        hit=("hit", "sum"),
    ).reset_index()

    grouped["ip"] = grouped["outs"] / 3.0

    # 0이닝 투수(기록 오류 등)는 나눗셈이 불가능하므로 제외
    grouped = grouped[grouped["ip"] > 0].copy()

    grouped["era"] = 9.0 * grouped["er"] / grouped["ip"]

    # FIP 상수 계산: 리그 전체 ERA와 리그 전체 FIP(상수 제외분)의 차이
    lg_era = 9.0 * grouped["er"].sum() / grouped["ip"].sum()
    fip_core = (13 * grouped["hr"] + 3 * grouped["bbhp"] - 2 * grouped["kk"]) \
        / grouped["ip"]
    lg_fip_core = (13 * grouped["hr"].sum() + 3 * grouped["bbhp"].sum()
                   - 2 * grouped["kk"].sum()) / grouped["ip"].sum()
    fip_const = lg_era - lg_fip_core   # 보통 3점대 초반이 나옵니다

    grouped["fip"] = fip_core + fip_const

    # 최근 소속팀: 그 투수의 '마지막 등판 경기'의 팀을 사용합니다.
    # (시즌 누적 팀 매핑보다 트레이드에 강건한 방식)
    last_team = (box.sort_values("date")
                 .groupby("pcode")["team"].last())
    grouped = grouped.merge(
        last_team.rename("team"), left_on="pcode", right_index=True, how="left"
    )

    return grouped
