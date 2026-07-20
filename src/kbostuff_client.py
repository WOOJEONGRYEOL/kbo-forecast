# -*- coding: utf-8 -*-
"""
kbostuff_client.py — KBO Talent(kbostuff.app) 세이버 지표 수집기
=================================================================

[역할]
  kbostuff.app이 내부적으로 쓰는 Supabase REST API를 직접 호출해서
  투수 구위(K-Stuff+), 타자 종합(Batter Metrics+), 정밀 wRC+ 데이터를
  받아오고, 이를 '팀 단위' 점수로 집계합니다.

[API 구조를 어떻게 알아냈나?]
  kbostuff.app은 React 기반 SPA라서 HTML을 긁어도 데이터가 없습니다.
  대신 브라우저 개발자도구 [Network] 탭에서 사이트가 호출하는
  Supabase REST 엔드포인트를 관찰해 그대로 재현했습니다.
  (Gemini 대화에서 말한 "방법 1: 내부 JSON API 직접 호출" 그대로입니다)

[사용하는 테이블]
  - pitching_metrics_v2_leaderboard : 투수별 K-Stuff+ v2, K-Control 등
  - batter_metrics_plus             : 타자별 종합+지표 (팀 코드 포함!)
  - regular_batter_advanced         : 타자별 순수 wRC+ (파크팩터 보정)
  - players                         : 선수코드(pcode) → 소속팀 매핑

[주의: 팀 매핑의 한계]
  players.current_team_code는 '현재' 소속팀입니다.
  시즌 중 트레이드된 선수는 과거 기록까지 새 팀으로 잡히는
  왜곡이 생길 수 있습니다. 팀 단위 집계에서는 오차가 작지만
  알고는 있어야 하는 한계입니다.
"""

import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests

import config


# Supabase는 한 번의 요청에 최대 1000행까지만 돌려줍니다 (기본 상한).
# 그보다 큰 테이블(예: players 1222명)은 offset을 옮겨가며
# 여러 번 나눠 받아야 전체 데이터를 얻을 수 있습니다.
_PAGE_SIZE = 1000


def _query(table: str, params: dict) -> list[dict]:
    """
    Supabase REST API에 GET 요청을 보내 JSON 리스트를 받아옵니다.
    1000행이 꽉 차서 돌아오면 "더 있다"는 뜻이므로,
    offset을 늘려가며 끝까지 페이지네이션합니다.

    Supabase(PostgREST) 쿼리 문법 참고:
      - 컬럼 선택 : select=*  또는 select=a,b,c
      - 필터      : season=eq.2026   (eq. / gte. / lte. 접두사)
      - 정렬      : order=k_stuff_v2.desc
    """
    headers = {
        # 둘 다 같은 공개 anon 키를 넣는 것이 Supabase 표준 호출 방식입니다
        "apikey": config.KBOSTUFF_ANON_KEY,
        "Authorization": f"Bearer {config.KBOSTUFF_ANON_KEY}",
        "User-Agent": config.USER_AGENT,
    }
    url = f"{config.KBOSTUFF_SUPABASE_URL}/{table}"

    all_rows: list[dict] = []
    offset = 0
    while True:
        page_params = {**params, "limit": _PAGE_SIZE, "offset": offset}
        resp = requests.get(
            url, headers=headers, params=page_params,
            timeout=config.REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        rows = resp.json()
        all_rows.extend(rows)
        time.sleep(config.REQUEST_DELAY_SEC)  # 연속 호출 시 서버 배려

        # 페이지가 꽉 차지 않았다면 마지막 페이지입니다
        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    return all_rows


# ──────────────────────────────────────────────
# 원본 데이터 가져오기
# ──────────────────────────────────────────────

def fetch_players() -> pd.DataFrame:
    """전체 선수 명단 (pcode → 현재 소속팀). 투수의 팀 매핑에 씁니다."""
    rows = _query("players", {"select": "pcode,name,current_team_code"})
    return pd.DataFrame(rows)


def fetch_pitching_metrics(season: int) -> pd.DataFrame:
    """
    투수별 K-Stuff+ v2 리더보드.

    K-Stuff+ 란? — 구속·무브먼트 등 '공 자체의 물리적 위력'을
    평가하는 지표. 100이 리그 평균, 110이면 상위권 구위.
    결과(방어율)가 아닌 원인(구위)을 보므로 미래 예측력이 높습니다.

    reliability 컬럼도 함께 받습니다. kbostuff가 표본량을 기준으로
    붙여준 신뢰도 등급(high/mid/low)으로, 2026시즌은 269명 중
    low가 118명(44%)입니다. 개인 단위 스크리닝에서는 이 등급을
    걸러야 노이즈에 속지 않습니다. (filter_reliable() 참고)
    """
    rows = _query(
        "pitching_metrics_v2_leaderboard",
        {
            # pitch_type_details: 구종별(직구/커브/포크...) 구위·구사율·헛스윙 +
            # 로케이션(edge/heart/waste)이 담긴 JSON. 아스널 카드에 씁니다.
            "select": ("pitcher_pcode,name,k_stuff_v2,k_control_v2,n_pitches,"
                       "k_rate,bb_rate,whiff_rate,csw_rate,avg_speed,"
                       "strike_pct,reliability,pitch_type_details"),
            "season": f"eq.{season}",
            "game_type": "eq.REGULAR",  # 정규시즌만 (시범경기 제외)
        },
    )
    return pd.DataFrame(rows)


# 신뢰도 등급 순서 (낮음 → 높음)
_RELIABILITY_RANK = {"low": 0, "mid": 1, "high": 2}


def filter_reliable(pit: pd.DataFrame, min_level: str = "mid") -> pd.DataFrame:
    """
    reliability 등급이 min_level 이상인 투수만 남깁니다.

    왜 필요한가? — 표본이 적은 투수의 K-Stuff+는 몇 개의 공에
    좌우돼 극단값이 나오기 쉽습니다. 팀 집계는 투구수 가중평균이라
    영향이 작지만, '억울한 투수 TOP 10' 같은 개인 순위표는
    저표본 투수가 상위권을 점령해 버립니다.

    등급이 없는(과거 캐시 등) 데이터는 걸러내지 않고 보존합니다.
    """
    if "reliability" not in pit.columns:
        return pit
    floor = _RELIABILITY_RANK.get(min_level, 1)
    rank = pit["reliability"].map(_RELIABILITY_RANK)
    # NaN(등급 미상)은 통과시킵니다 — 정보가 없다고 버리진 않음
    return pit[rank.isna() | (rank >= floor)]


def fetch_batter_metrics(season: int) -> pd.DataFrame:
    """
    타자별 Batter Metrics+ (팀 코드가 이미 들어있어 매핑이 필요 없음).

    overall_plus — 선구(Eye), 컨택(Vision), 타격(Hit), 파워(Power),
    주루(Baserunning)를 종합한 100 기준 지표입니다.
    """
    rows = _query(
        "batter_metrics_plus",
        {
            # 팀 집계(main.py)와 개인 평가(players.py)가 함께 쓰는 테이블이라
            # 스킬 5종 + 인플레이 기대치(wOBA/xwOBA) + 유형 라벨까지 다 받습니다
            "select": ",".join([
                "pcode", "player_name", "team_code", "n_pa", "n_inplay",
                # 5툴 레이더
                "overall_plus", "eye_plus", "vision_plus", "hit_plus",
                "power_plus", "hr_plus", "baserunning_plus",
                # 인플레이 기대치 + 운
                "woba_inplay", "xwoba_inplay", "babip", "player_type",
                # 플레이트 디서플린(접근/타격안): 레이더 카드 상세용
                "chase_rate", "zone_swing_rate", "whiff_rate", "contact_rate",
                "iso_inplay", "expected_hr_rate", "actual_hr_rate",
                "sb_plus", "bsr_runs", "avg_speed_faced",
            ]),
            "season": f"eq.{season}",
            "n_pa": "gte.30",  # 표본 30타석 미만은 노이즈라 제외
        },
    )
    return pd.DataFrame(rows)


def fetch_batter_wrc(season: int) -> pd.DataFrame:
    """
    타자별 순수 wRC+ (wrc_plus_pure).

    wRC+ 란? — '리그 평균 대비 득점 창출력'. 100이 평균, 130이면
    평균보다 30% 많은 득점을 만들어 낸다는 뜻.
    'pure' 버전은 구장 크기(파크팩터)와 타구 비거리까지 반영해
    잠실 같은 큰 구장의 타자가 손해 보지 않도록 보정한 값입니다.
    """
    rows = _query(
        "regular_batter_advanced",
        {
            # pure(비거리 기반 보정)와 event(실제 사건 기반)를 함께 받아야
            # 둘의 차이로 '구장에 갇힌 타자'를 찾을 수 있습니다
            "select": "pcode,pa,wrc_plus_pure,wrc_plus_event,wrc_plus_diff,woba,park_factor",
            "season": f"eq.{season}",
            "pa": "gte.30",
        },
    )
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 경기별 구위 로그 (point-in-time 스냅샷)
# ──────────────────────────────────────────────
#
# [왜 중요한가]
#   리더보드(pitching_metrics_v2_leaderboard)는 '시즌 누적' 한 덩어리라
#   과거 특정 시점의 구위를 알 수 없었습니다. 그래서
#     · 모멘텀 지수의 괴리율은 '최근 10경기'인데 구위 항은 '시즌 누적' —
#       서로 다른 시간축의 값을 더하고 있었고,
#     · 구위 항이 미래 예측에 실제로 기여하는지 백테스트할 수 없었습니다.
#   pitching_metrics_v2_game_log는 경기 단위 K-Stuff+를 2021년까지
#   제공하므로, 위 두 문제를 모두 풉니다.
#
# [캐시 전략]
#   끝난 경기의 지표는 거의 바뀌지 않지만, kbostuff가 며칠 뒤
#   재계산해 덮어쓰는 경우가 있어 최근 며칠은 다시 받아 갱신합니다.
#   (전량 재수집은 3만 행이라 낭비 — 증분만 받습니다)

def _gamelog_cache_path(season: int) -> Path:
    return Path(config.DATA_DIR) / f"kbostuff_pitch_gamelog_{season}.csv"


def fetch_pitching_game_log(season: int, refresh_days: int = 3) -> pd.DataFrame:
    """
    투수별 '경기 단위' K-Stuff+ 로그를 받아옵니다.

    한 행 = 한 투수의 한 경기 등판.
      pitcher_pcode, game_id, game_date, k_stuff_v2, k_control_v2,
      n_pitches, whiff_rate, csw_rate, avg_speed

    첫 실행은 시즌 전체(약 4~6천 행)를 받고, 이후에는 캐시의
    마지막 날짜에서 refresh_days만큼 거슬러 올라간 시점부터만
    다시 받습니다. 같은 (투수, 경기) 조합은 새로 받은 값으로 덮어씁니다.

    ⚠️ kbostuff의 게임로그는 하루 이틀 늦게 채워집니다.
      "어제 = 오늘-1"을 가정하지 말고, 항상 캐시의 마지막 날짜를
      기준으로 증분을 잡아야 구멍이 생기지 않습니다.
    """
    cache = _gamelog_cache_path(season)
    # pcode/game_id는 앞자리 0이 있을 수 있어 반드시 문자열로 읽습니다
    dtypes = {"pitcher_pcode": str, "game_id": str, "game_date": str}

    old = pd.DataFrame()
    if cache.exists():
        old = pd.read_csv(cache, dtype=dtypes)

    params = {
        "select": ("pitcher_pcode,game_id,game_date,k_stuff_v2,k_control_v2,"
                   "n_pitches,whiff_rate,csw_rate,avg_speed"),
        "season": f"eq.{season}",
        "game_type": "eq.REGULAR",
        "order": "game_date.asc",
    }

    if not old.empty:
        last = pd.to_datetime(old["game_date"]).max()
        since = (last - timedelta(days=refresh_days)).strftime("%Y-%m-%d")
        params["game_date"] = f"gte.{since}"

    new = pd.DataFrame(_query("pitching_metrics_v2_game_log", params))
    if not new.empty:
        new = new.astype({"pitcher_pcode": str, "game_id": str,
                          "game_date": str})

    # keep="last" — 새로 받은 값이 캐시된 옛 값을 덮어씁니다
    merged = (pd.concat([old, new], ignore_index=True)
              .drop_duplicates(["pitcher_pcode", "game_id"], keep="last")
              .sort_values(["game_date", "game_id"])
              .reset_index(drop=True))

    cache.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(cache, index=False)
    print(f"  → 경기별 구위 로그 {len(merged)}행 "
          f"(신규/갱신 {len(new)}, 캐시 {len(old)})")
    return merged


def daily_league_stuff(game_log: pd.DataFrame) -> pd.Series:
    """
    날짜별 '리그 전체' K-Stuff+ (투구수 가중평균).

    재센터링의 기준선이자, 상류 데이터 이상을 잡아내는 감시 장치입니다.
    정상이라면 매일 100 언저리에서 ±1 정도로만 흔들립니다.
    """
    g = game_log.dropna(subset=["k_stuff_v2", "n_pitches"]).copy()
    g["w"] = g["k_stuff_v2"] * g["n_pitches"]
    agg = g.groupby("game_date").agg(w=("w", "sum"), p=("n_pitches", "sum"))
    return (agg["w"] / agg["p"]).rename("league_stuff")


def infer_pitcher_teams(game_log: pd.DataFrame) -> pd.DataFrame:
    """
    게임로그만으로 (투수, 경기) → 소속팀을 복원합니다. 추가 요청 0회.

    [원리]
      game_id는 'YYYYMMDD + 원정팀 + 홈팀 + 게임번호 + 연도' 구조입니다.
        20260408HHSK02026  →  원정 HH, 홈 SK
      한 투수가 등판한 모든 경기에는 '자기 팀'이 항상 들어있고,
      상대팀은 매번 달라집니다. 따라서 등판 기록 전체에서
      가장 자주 등장하는 팀 = 그 투수의 소속팀입니다.
      (9개 상대팀은 각각 1/9 빈도로만 나오므로 구분이 아주 선명합니다)

    [왜 이렇게까지 하나]
      과거 시즌 백테스트에는 네이버 박스스코어가 필요한데,
      5시즌치를 새로 받으면 3,600회 요청입니다. 이 방식이면
      이미 받아둔 게임로그만으로 끝납니다.

    ⚠️ 시즌 중 트레이드된 투수는 두 팀이 섞여 다수결로 눌립니다.
      팀 단위 집계에서는 영향이 작지만, 정밀한 현재 시점 분석에는
      박스스코어 기반(team_stuff_by_game)을 쓰는 편이 정확합니다.

    반환: pitcher_pcode, team
    """
    if game_log.empty:
        return pd.DataFrame(columns=["pitcher_pcode", "team"])

    gid = game_log["game_id"].astype(str)
    long = pd.concat([
        pd.DataFrame({"pitcher_pcode": game_log["pitcher_pcode"],
                      "cand": gid.str[8:10]}),    # 원정
        pd.DataFrame({"pitcher_pcode": game_log["pitcher_pcode"],
                      "cand": gid.str[10:12]}),   # 홈
    ])
    counts = long.value_counts(["pitcher_pcode", "cand"]).reset_index(name="n")
    best = counts.sort_values("n").drop_duplicates("pitcher_pcode", keep="last")
    return best[["pitcher_pcode", "cand"]].rename(columns={"cand": "team"})


def team_stuff_by_game(game_log: pd.DataFrame,
                       box: pd.DataFrame,
                       recenter: bool = True) -> pd.DataFrame:
    """
    경기별 구위 로그에 '팀'을 붙이고 (팀, 경기) 단위로 집계합니다.

    [팀을 어떻게 붙이나]
      게임로그에는 소속팀이 없습니다(opponent_team 컬럼은 전량 null).
      대신 네이버 박스스코어가 경기별 등판 투수의 팀을 갖고 있고,
      두 소스의 game_id·pcode 체계가 동일하므로 그대로 조인됩니다.
      → players.current_team_code('현재' 팀)를 쓸 때 생기던
        트레이드 왜곡이 여기서는 원천적으로 없습니다.

    [recenter — 왜 필요한가 (중요)]
      K-Stuff+의 절대 스케일은 상류(kbostuff)에서 이따금 바뀝니다.
      실제로 2026시즌엔 4/3 Probit 제거, 6/19 1SD=5점 재정규화가 있었고,
      7/16 이후로는 리그 평균이 하루아침에 100 → 108~111로 뛰었습니다
      (2021~2025는 전 월이 99.8~100.8로 안정적이므로 이는 야구가 아니라
       상류 재처리 아티팩트입니다).

      서로 다른 시점의 값을 같은 저울에 올리려면 절대값을 쓰면 안 됩니다.
      그래서 각 등판을 '그날 리그 평균 대비'로 바꿔 100 기준으로 되돌립니다.
        조정값 = K-Stuff+ − (그날 리그 평균) + 100
      같은 날 팀 간 비교는 그대로 보존되고(모두 같은 상수를 빼므로),
      시점이 다른 값끼리도 비교할 수 있게 됩니다.
      → 상류가 또 스케일을 바꿔도 파이프라인은 흔들리지 않습니다.

    반환: 한 행 = (team, game_id, game_date, stuff_wsum, pitches)
      stuff_wsum = Σ(K-Stuff+ × 투구수),  pitches = Σ투구수
      → 나중에 최근 N경기 구간에서 stuff_wsum.sum()/pitches.sum() 하면
        곧바로 '투구수 가중평균 팀 구위'가 됩니다.
    """
    if game_log.empty or box.empty:
        return pd.DataFrame(
            columns=["team", "game_id", "game_date", "stuff_wsum", "pitches"])

    gl = game_log.dropna(subset=["k_stuff_v2", "n_pitches"]).copy()

    if recenter:
        # 기준선은 조인 전 '전체' 등판으로 계산합니다.
        # (박스스코어와 매칭 안 된 등판도 리그 평균에는 기여해야 정확)
        baseline = daily_league_stuff(gl)
        gl["k_stuff_v2"] = (gl["k_stuff_v2"]
                            - gl["game_date"].map(baseline) + 100.0)

    # 박스스코어에서 (경기, 투수) → 팀 매핑만 추립니다
    team_map = (box[["game_id", "pcode", "team"]]
                .astype({"game_id": str, "pcode": str})
                .drop_duplicates(["game_id", "pcode"]))

    m = gl.merge(
        team_map, left_on=["game_id", "pitcher_pcode"],
        right_on=["game_id", "pcode"], how="inner",
    )

    m["stuff_wsum"] = m["k_stuff_v2"] * m["n_pitches"]

    return (m.groupby(["team", "game_id", "game_date"], as_index=False)
            .agg(stuff_wsum=("stuff_wsum", "sum"), pitches=("n_pitches", "sum"))
            .sort_values(["team", "game_date"]))


def team_stuff_by_game_inferred(game_log: pd.DataFrame,
                                recenter: bool = True) -> pd.DataFrame:
    """
    박스스코어 없이 게임로그만으로 (팀, 경기) 구위를 집계합니다.

    과거 시즌 백테스트용입니다. 팀 귀속은 infer_pitcher_teams()로 복원하며,
    2026시즌 기준 팀 집계 상관 0.9999 / 최대오차 0.014점으로
    박스스코어 기반과 사실상 동일합니다.
    (오차는 등판 1회 투수에만 생기는데, 투구수 가중이라 영향이 없습니다)
    """
    teams = infer_pitcher_teams(game_log)
    g = game_log.merge(teams, on="pitcher_pcode", how="inner")
    pseudo_box = g.rename(columns={"pitcher_pcode": "pcode"})[
        ["game_id", "pcode", "team"]]
    return team_stuff_by_game(game_log, pseudo_box, recenter=recenter)


def rolling_team_stuff(by_game: pd.DataFrame,
                       window: int | None = config.ROLLING_WINDOW) -> pd.Series:
    """
    팀별 '최근 window경기' 구위 점수 (투구수 가중평균).

    window=None이면 시즌 전체 누적 — 기존 team_pitching_score()와
    같은 성격의 값이 됩니다(집계 경로만 게임로그로 바뀜).

    반환: index=팀코드, value=팀 K-Stuff+
    """
    if by_game.empty:
        return pd.Series(dtype=float)

    out = {}
    for team, g in by_game.groupby("team"):
        g = g.sort_values("game_date")
        recent = g if window is None else g.tail(window)
        pitches = recent["pitches"].sum()
        if pitches > 0:
            out[team] = recent["stuff_wsum"].sum() / pitches
    return pd.Series(out, dtype=float)


# ──────────────────────────────────────────────
# 팀 단위 집계
# ──────────────────────────────────────────────

def team_pitching_score(season: int, players: pd.DataFrame) -> pd.Series:
    """
    팀별 '투수진 구위 점수' = 소속 투수들의 K-Stuff+를
    투구수(n_pitches)로 가중평균한 값.

    왜 가중평균? — 많이 던진 투수일수록 팀 실점에 미치는
    영향이 크기 때문입니다. 단순 평균을 쓰면 1이닝 던진
    신인의 지표가 에이스와 같은 무게를 갖게 됩니다.
    """
    pit = fetch_pitching_metrics(season)

    # 투수 테이블에는 팀 정보가 없어서 players 명단과 조인합니다
    pit = pit.merge(
        players.rename(columns={"pcode": "pitcher_pcode"}),
        on="pitcher_pcode",
        how="left",
    )
    pit = pit.dropna(subset=["current_team_code", "k_stuff_v2"])

    def weighted(group: pd.DataFrame) -> float:
        w = group["n_pitches"]
        return (group["k_stuff_v2"] * w).sum() / w.sum()

    return pit.groupby("current_team_code").apply(weighted, include_groups=False)


def summarize_location(pit: pd.DataFrame) -> pd.DataFrame:
    """
    pitch_type_details JSON → 투수 단위 '로케이션' 요약.

    [이 데이터는 이미 받고 있었습니다]
      아스널 카드용으로 pitch_type_details를 받으면서 usage_pct와 group만
      꺼내 쓰고, 정작 kbostuff가 2축의 한 축으로 내세우는 로케이션
      필드들(edge/heart/waste, k_location_v3)은 버리고 있었습니다.
      추가 요청 없이 그대로 살려 쓰는 집계입니다.

    [각 필드의 뜻]
      edge_pct       보더라인(스트라이크존 가장자리) 공략 비율
      heart_pct      한가운데 실투 비율            ← 높을수록 위험
      waste_pct      명백한 볼 비율                ← 높을수록 볼넷 경향
      k_location_v3  구종별 로케이션 점수 (100 = 리그 평균)

    ⚠️ edge + heart + waste 는 1이 되지 않습니다 (2026 평균 합 0.70).
      나머지는 별도 구간이라, 세 값을 구성비로 정규화하면 틀립니다.

    반환: pitcher_pcode, k_location, edge_pct, heart_pct, waste_pct, top_pitch
      (모두 구종별 투구 수 n 으로 가중평균)
    """
    rows = []
    for r in pit.itertuples():
        d = getattr(r, "pitch_type_details", None)
        if not isinstance(d, dict) or not d:
            continue
        total = sum((p.get("n") or 0) for p in d.values())
        if total <= 0:
            continue

        def wavg(key: str) -> float:
            return sum((p.get(key) or 0) * (p.get("n") or 0)
                       for p in d.values()) / total

        rows.append({
            "pitcher_pcode": r.pitcher_pcode,
            "k_location": wavg("k_location_v3"),
            "edge_pct": wavg("edge_pct"),
            "heart_pct": wavg("heart_pct"),
            "waste_pct": wavg("waste_pct"),
            "top_pitch": _top_pitch(d),
        })
    return pd.DataFrame(rows)


def _top_pitch(details) -> str:
    """구종 상세 JSON에서 구사율 1위 구종 그룹 이름을 뽑습니다."""
    if not isinstance(details, dict) or not details:
        return "-"
    best = max(details.values(), key=lambda d: d.get("usage_pct", 0) or 0)
    return best.get("group", "-")


def team_rotation(season: int, rotation: pd.DataFrame):
    """
    선발 로테이션 기반 팀 투수력 점수와 로테이션 상세를 함께 반환합니다.

    rotation : boxscore.identify_rotation() 결과 (pcode, team, name, starts)

    반환:
      score  : Series[팀] = 선발 K-Stuff+ (선발 등판수 가중평균)
      detail : dict[팀] = [{name, starts, stuff, top(주무기)}, ...]

    '전체 투수진'이 아니라 '실제로 선발로 나오는 투수들'만 봐서,
    다음 시리즈 선발 매치업을 염두에 둔 투수력을 잽니다.
    """
    pit = fetch_pitching_metrics(season).rename(
        columns={"pitcher_pcode": "pcode"})
    # rotation에 이미 name이 있으므로 pit에서는 지표 컬럼만 가져와 충돌 방지
    m = rotation.merge(
        pit[["pcode", "k_stuff_v2", "pitch_type_details"]],
        on="pcode", how="left").dropna(subset=["k_stuff_v2"])

    def weighted(g):
        return (g["k_stuff_v2"] * g["starts"]).sum() / g["starts"].sum()

    score = m.groupby("team").apply(weighted, include_groups=False)

    detail = {}
    for team, g in m.sort_values("starts", ascending=False).groupby("team"):
        detail[team] = [
            {
                "name": r["name"], "starts": int(r["starts"]),
                "stuff": round(float(r["k_stuff_v2"]), 1),
                "top": _top_pitch(r["pitch_type_details"]),
            }
            for _, r in g.iterrows()
        ]
    return score, detail


def team_batting_score(season: int) -> pd.DataFrame:
    """
    팀별 '타선 점수' 두 가지를 한 번에 계산합니다.

      bat_overall_plus : Batter Metrics+ overall을 타석수 가중평균
      bat_wrc_pure     : 순수 wRC+를 타석수 가중평균

    반환: index=팀코드, columns=[bat_overall_plus, bat_wrc_pure]
    """
    bat = fetch_batter_metrics(season)
    wrc = fetch_batter_wrc(season)

    # 두 테이블 모두 선수코드(pcode) 기준이므로 조인해서 팀 코드를 공유
    merged = bat.merge(wrc, on="pcode", how="left")

    def agg(group: pd.DataFrame) -> pd.Series:
        w_pa = group["n_pa"]
        out = {
            "bat_overall_plus": (group["overall_plus"] * w_pa).sum() / w_pa.sum()
        }
        # wRC+는 조인이 안 된(결측) 선수를 빼고 따로 가중평균
        ok = group.dropna(subset=["wrc_plus_pure"])
        if len(ok) > 0:
            out["bat_wrc_pure"] = (
                (ok["wrc_plus_pure"] * ok["pa"]).sum() / ok["pa"].sum()
            )
        else:
            out["bat_wrc_pure"] = float("nan")
        return pd.Series(out)

    return merged.groupby("team_code").apply(agg, include_groups=False)


# ──────────────────────────────────────────────
# FCB (협조적 게임이론 기반 승리 기여)
# ──────────────────────────────────────────────

def fetch_fcb(season: int) -> pd.DataFrame:
    """
    타자별 FCB 승리기여 리더보드.

    FCB(Fair Contribution Breakdown)란? — 협조적 게임이론의 Shapley value를
    야구에 적용한 kbostuff 고유 지표입니다. '득점이 만들어진 이닝'에서
    각 타자가 실제로 승리에 기여한 몫을 공정 분배해 합산합니다.
      · wins_contributed : 누적 승리 기여 (팀 승수 중 이 타자 몫)
      · total_src        : Situational Run Contribution 합
      · avg_src_per_game : 경기당 상황 기여
    ⚠️ '클러치'는 세이버메트릭스에서 잘 지속되지 않는(예측력 낮은) 성질이라,
      미래 예측이 아니라 '지금까지 무슨 일이 있었나'를 설명하는 지표로 씁니다.
    """
    rows = _query(
        "fcb_season_leaderboard",
        {
            "select": ("batter_pcode,batter_name,team_code,wins_contributed,"
                       "total_src,avg_src_per_game,games_played,total_pa,"
                       "positive_src_innings,negative_src_innings"),
            "season": f"eq.{season}",
            "game_type": "eq.REGULAR",
        },
    )
    return pd.DataFrame(rows)


def team_fcb_score(season: int) -> pd.Series:
    """
    팀별 누적 승리기여(FCB) 합.
    팀 소속 타자들의 wins_contributed를 그대로 더합니다.
    (팀이 실제로 얼마나 '이기는 순간'에 강했는지를 설명)

    반환: index=팀코드, value=팀 누적 승리기여
    """
    fcb = fetch_fcb(season)
    if fcb.empty:
        return pd.Series(dtype=float)
    return fcb.groupby("team_code")["wins_contributed"].sum()
