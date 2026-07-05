# -*- coding: utf-8 -*-
"""
player_eval.py — 투수 / 타자 개인 평가 모델
=============================================

Gemini 대화의 [추천 1]과 [추천 2]를 구현한 모듈입니다.

■ 투수 모델 — "결과(방어율)에 속지 말고, 공 자체의 위력을 측정하자"
    원인 지표 : K-Stuff+ (kbostuff.app — 구속·무브먼트 기반 구위)
    결과 지표 : ERA / FIP (네이버 박스스코어 시즌 합산 — boxscore.py)
    두 축을 교차하면 4가지 유형이 나옵니다:

                     │ 성적 좋음(ERA 낮음) │ 성적 나쁨(ERA 높음)
      ───────────────┼─────────────────────┼─────────────────────
      구위 좋음(Stuff↑)│ ✅ 진짜 에이스        │ 📈 억울한 투수 (반등 후보)
      구위 나쁨(Stuff↓)│ ⚠️ 시한폭탄 (하락 경계)│ 😞 그냥 부진

■ 타자 모델 — "운을 제거한 순수 스킬 발라내기"
    ① 인플레이 운 진단 : wOBA(실제) − xwOBA(타구질 기반 기대치)
       실제가 기대보다 훨씬 높으면 → 거품 (곧 식을 타자)
       실제가 기대보다 훨씬 낮으면 → 불운 (곧 터질 타자)
    ② 파워 유형 분류 : Power+ × HR+ 교차 (Gap Power vs HR Specialist)
    ③ 구장 피해자 탐지 : 순수 wRC+ − 이벤트 wRC+ (잠실 등 큰 구장 보정)
"""

import numpy as np
import pandas as pd

import config

# ── 스크리닝 기준값 (조절 가능한 다이얼) ──────────────────

MIN_IP = 20          # 투수 최소 이닝 (표본 미달 노이즈 제거)
MIN_PITCHES = 150    # kbostuff 지표 신뢰를 위한 최소 투구 수
STUFF_HIGH = 105     # 이 이상이면 '구위 좋음' (100=리그 평균)
STUFF_LOW = 97       # 이 이하면 '구위 아쉬움'

MIN_PA = 100         # 타자 최소 타석
BABIP_GAP = 0.040    # BABIP가 리그 평균에서 이만큼 벗어나면 운으로 판정


# ══════════════════════════════════════════════════════════
# 투수 평가
# ══════════════════════════════════════════════════════════

def evaluate_pitchers(season_stats: pd.DataFrame,
                      stuff_metrics: pd.DataFrame) -> pd.DataFrame:
    """
    박스스코어 시즌 성적(ERA/FIP)과 kbostuff 구위 지표(K-Stuff+)를
    선수 코드(pcode)로 조인하고 유형을 분류합니다.

    season_stats  : boxscore.season_pitcher_stats() 결과
    stuff_metrics : kbostuff_client.fetch_pitching_metrics() 결과
    """
    df = season_stats.merge(
        stuff_metrics.rename(columns={"pitcher_pcode": "pcode"}),
        on="pcode", how="inner", suffixes=("", "_ks"),
    )

    # 표본 필터: 이닝과 투구 수가 모두 충분한 투수만
    df = df[(df["ip"] >= MIN_IP) & (df["n_pitches"] >= MIN_PITCHES)].copy()

    # 리그 평균 ERA (같은 표본 기준) — '성적 좋음/나쁨'의 기준선
    lg_era = 9.0 * df["er"].sum() / df["ip"].sum()

    def classify(row) -> str:
        good_stuff = row["k_stuff_v2"] >= STUFF_HIGH
        bad_stuff = row["k_stuff_v2"] <= STUFF_LOW
        good_result = row["era"] < lg_era
        if good_stuff and good_result:
            return "✅ 진짜 에이스"
        if good_stuff and not good_result:
            return "📈 억울한 투수 (반등 후보)"
        if bad_stuff and good_result:
            return "⚠️ 시한폭탄 (하락 경계)"
        if bad_stuff and not good_result:
            return "😞 부진 (구위부터 문제)"
        return "➖ 평균권"

    df["type"] = df.apply(classify, axis=1)

    # ERA-FIP 격차: +면 수비/운의 피해자, -면 수혜자
    # (FIP는 수비와 무관한 사건만 반영하므로, 이 차이가 곧 '외부 요인')
    df["era_fip_gap"] = df["era"] - df["fip"]

    df["team_name"] = df["team"].map(config.TEAM_NAMES).fillna(df["team"])

    cols = ["pcode", "name", "team", "team_name", "ip", "era", "fip",
            "era_fip_gap", "k_stuff_v2", "k_control_v2", "n_pitches",
            "kk", "bb", "hr", "type",
            # 아스널 카드용: 구종별 상세 JSON + 전체 헛스윙/CSW/평균구속
            "pitch_type_details", "whiff_rate", "csw_rate", "avg_speed"]
    return df[cols].sort_values("k_stuff_v2", ascending=False)


def pitcher_screens(evaluated: pd.DataFrame) -> dict:
    """리포트용 스크리닝 목록 3종을 뽑아 돌려줍니다."""
    unlucky = evaluated[evaluated["type"].str.contains("억울한")] \
        .sort_values("k_stuff_v2", ascending=False)
    timebomb = evaluated[evaluated["type"].str.contains("시한폭탄")] \
        .sort_values("era")
    # 수비/운 피해 순위: FIP 대비 ERA가 크게 높은 투수
    defense_victim = evaluated[evaluated["era_fip_gap"] > 0.7] \
        .sort_values("era_fip_gap", ascending=False)
    return {
        "unlucky": unlucky,
        "timebomb": timebomb,
        "defense_victim": defense_victim,
    }


# ══════════════════════════════════════════════════════════
# 타자 평가
# ══════════════════════════════════════════════════════════

def evaluate_batters(bat_metrics: pd.DataFrame,
                     bat_wrc: pd.DataFrame,
                     fcb: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    kbostuff의 타자 지표 두(세) 테이블을 조인해 운/유형/구장/클러치 진단을 붙입니다.

    bat_metrics : batter_metrics_plus (스킬 + 인플레이 기대치 + 디서플린)
    bat_wrc     : regular_batter_advanced (wRC+ pure/event, 파크팩터)
    fcb         : fcb_season_leaderboard (선택) — 승리기여/상황 기여
    """
    df = bat_metrics.merge(bat_wrc, on="pcode", how="left")
    if fcb is not None and not fcb.empty:
        df = df.merge(
            fcb.rename(columns={"batter_pcode": "pcode"})[
                ["pcode", "wins_contributed", "total_src", "avg_src_per_game"]
            ],
            on="pcode", how="left",
        )
    else:
        df["wins_contributed"] = float("nan")
        df["total_src"] = float("nan")
        df["avg_src_per_game"] = float("nan")
    df = df[df["n_pa"] >= MIN_PA].copy()

    # ── ① 인플레이 운 진단 (BABIP 기반) ──
    # BABIP = 인플레이 타구의 안타 비율. 타자가 어쩔 수 없는 요소
    # (수비 위치, 바가지 안타, 호수비)에 크게 좌우되어, 리그 평균
    # (약 .300)에서 크게 벗어난 값은 평균으로 회귀하는 경향이 강합니다.
    # → 세이버메트릭스에서 가장 오래되고 검증된 '운 탐지기'입니다.
    #
    # 참고: 원래는 xwOBA 잔차(실제-기대)를 쓰려 했으나, kbostuff의
    #   xwoba_inplay는 wOBA와 상관이 0.2에 불과하고 분산도 1/4 수준이라
    #   운 판정 근거로 쓰기 어렵다고 판단해 BABIP로 교체했습니다.
    #   (xwOBA 컬럼은 참고용으로 데이터에 남겨둡니다)
    ok = df["babip"].notna() & df["n_inplay"].notna()
    # 인플레이 타구 수로 가중한 리그 평균 BABIP가 기준선
    lg_babip = (df.loc[ok, "babip"] * df.loc[ok, "n_inplay"]).sum() \
        / df.loc[ok, "n_inplay"].sum()
    df["luck"] = df["babip"] - lg_babip
    df.attrs["lg_babip"] = lg_babip   # 대시보드에서 기준선 표시용

    def luck_label(v) -> str:
        if pd.isna(v):
            return "➖ 판정 불가"
        if v > BABIP_GAP:
            return "🫧 거품 주의 (BABIP 고평가, 곧 식을 수 있음)"
        if v < -BABIP_GAP:
            return "💎 저평가 (BABIP 불운, 곧 터질 수 있음)"
        return "➖ 적정"

    df["luck_type"] = df["luck"].apply(luck_label)

    # ⚠️ 해석 주의: 발 빠른 타자나 라인드라이브 히터는 '실력으로'
    #   높은 BABIP를 유지하기도 합니다. 스크리닝 결과는 후보 목록일 뿐,
    #   개별 선수는 타구 성향과 함께 봐야 합니다.

    # ── ② 파워 유형 분류 (Gemini 추천 2의 교차 분석) ──
    # power_plus : 장타 생산력 전반 (2루타·3루타 포함)
    # hr_plus    : 순수 홈런 파워
    def power_type(row) -> str:
        p, h = row["power_plus"], row["hr_plus"]
        if pd.isna(p) or pd.isna(h):
            return "➖"
        if p >= 105 and h >= 105:
            return "💪 컴플리트 파워 (장타+홈런 모두)"
        if p >= 105 and h < 100:
            return "🏟️ 갭 파워 (2·3루타형 스프레이 히터)"
        if p < 100 and h >= 105:
            return "🎰 홈런 스페셜리스트 (걸리면 넘어감)"
        return "➖ 표준"

    df["power_type"] = df.apply(power_type, axis=1)

    # ── ③ 구장 피해자 탐지 ──
    # wrc_plus_pure  : 타구 비거리 기반, 구장 영향 제거판
    # wrc_plus_event : 실제 일어난 사건 기반 (구장 영향 포함)
    # pure가 event보다 높다 = 큰 구장 때문에 손해 보는 중
    df["park_gap"] = df["wrc_plus_pure"] - df["wrc_plus_event"]

    df["team_name"] = df["team_code"].map(config.TEAM_NAMES) \
        .fillna(df["team_code"])

    cols = ["pcode", "player_name", "team_code", "team_name", "n_pa",
            "n_inplay", "overall_plus", "eye_plus", "vision_plus", "hit_plus",
            "power_plus", "hr_plus", "baserunning_plus",
            "woba_inplay", "xwoba_inplay", "luck", "luck_type", "babip",
            "power_type", "wrc_plus_pure", "wrc_plus_event", "park_gap",
            "park_factor", "player_type",
            # 레이더 카드용 플레이트 디서플린
            "chase_rate", "zone_swing_rate", "whiff_rate", "contact_rate",
            "iso_inplay", "sb_plus",
            # FCB 클러치
            "wins_contributed", "total_src", "avg_src_per_game"]
    out = df[cols].sort_values("overall_plus", ascending=False)
    out.attrs["lg_babip"] = df.attrs.get("lg_babip")
    return out


def batter_screens(evaluated: pd.DataFrame) -> dict:
    """리포트용 타자 스크리닝 목록을 뽑습니다."""
    undervalued = evaluated[evaluated["luck"] < -BABIP_GAP] \
        .sort_values("luck")
    bubble = evaluated[evaluated["luck"] > BABIP_GAP] \
        .sort_values("luck", ascending=False)
    park_victim = evaluated[evaluated["park_gap"] >= 5] \
        .sort_values("park_gap", ascending=False)
    # 🔥 승부처 강자: FCB 누적 승리기여 상위 (설명형 지표)
    clutch = evaluated.dropna(subset=["wins_contributed"]) \
        .sort_values("wins_contributed", ascending=False)
    return {
        "undervalued": undervalued,   # 💎 곧 터질 타자
        "bubble": bubble,             # 🫧 곧 식을 타자
        "park_victim": park_victim,   # 🏟️ 구장에 갇힌 타자
        "clutch": clutch,             # 🔥 승부처 승리기여 최상위
    }
