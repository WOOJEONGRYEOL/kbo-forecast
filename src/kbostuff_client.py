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
    """
    rows = _query(
        "pitching_metrics_v2_leaderboard",
        {
            # pitch_type_details: 구종별(직구/커브/포크...) 구위·구사율·헛스윙이
            # 담긴 JSON. '아스널(구종 구성) 카드'에 씁니다.
            "select": ("pitcher_pcode,name,k_stuff_v2,k_control_v2,n_pitches,"
                       "k_rate,bb_rate,whiff_rate,csw_rate,avg_speed,pitch_type_details"),
            "season": f"eq.{season}",
            "game_type": "eq.REGULAR",  # 정규시즌만 (시범경기 제외)
        },
    )
    return pd.DataFrame(rows)


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
