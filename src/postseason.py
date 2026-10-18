# -*- coding: utf-8 -*-
"""
postseason.py — KBO 가을야구(포스트시즌) 대진·예측·시리즈 승리확률
====================================================================

정규시즌 144경기가 끝나면 상위 5팀이 '사다리' 토너먼트를 벌인다(2026 기준):

    와일드카드(WC)  4위 vs 5위     — 최대 2경기(둘 다 4위 홈). 4위 1승/무, 5위 2승
       └→ 준PLAYOFF  3위 vs WC승자  — 5전 3선승
          └→ PLAYOFF  2위 vs 준PO승자 — 5전 3선승
             └→ 한국시리즈 1위 vs PO승자 — 7전 4선승

대진은 정규 최종순위로 확정. 각 라운드 일정은 앞 라운드가 끝나야 잡히므로
네이버 피드에서 포스트시즌 gameId(날짜 접두가 아닌 4444… 등)를 라운드별로 모은다.

예측: 경기별 기대스코어·승률은 game_projection 모델 재사용(정규시즌 팀 강도 기준).
시리즈 승리확률: 경기별 승률 → 몬테카를로(라운드 포맷·KBO 홈 배분 반영).
⚠️ 단일경기 정직한 천장 ~56% — 단기전은 분산이 커 '예언'이 아니라 확률·서술.
"""

import datetime
from collections import defaultdict

import numpy as np

import config

# 라운드 정의: (키, 한글명, 포맷, 상위시드, 하위시드 or None=앞라운드 승자)
#   fmt: "wc"(최대2·4위홈2), "bo5"(5전3선승), "bo7"(7전4선승)
ROUNDS = [
    ("wc",  "와일드카드",   "wc",  4, 5),
    ("spo", "준플레이오프", "bo5", 3, None),   # vs WC 승자
    ("po",  "플레이오프",   "bo5", 2, None),   # vs 준PO 승자
    ("ks",  "한국시리즈",   "bo7", 1, None),   # vs PO 승자
]

# 홈 배분(상위시드 관점, 1=상위홈/0=하위홈). KBO 2026 기준.
HOME_PATTERN = {
    "wc":  [1, 1],                      # 둘 다 4위(상위) 홈
    "bo5": [1, 1, 0, 0, 1],             # 상위 1·2·5차전 홈
    "bo7": [1, 1, 0, 0, 0, 1, 1],       # 상위 1·2·6·7차전 홈
}
WINS_NEEDED = {"wc": None, "bo5": 3, "bo7": 4}   # wc는 특수 처리


def final_standings(reg_games: list) -> list:
    """정규시즌 최종 순위(1위→꼴찌) 팀코드 리스트. 승률=승/(승+패), 무승부 제외."""
    W, L = defaultdict(int), defaultdict(int)
    for g in reg_games:
        if g.get("statusCode") not in ("RESULT", "ENDED") or g.get("cancel"):
            continue
        hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
        if hs is None or as_ is None:
            continue
        h, a = g["homeTeamCode"], g["awayTeamCode"]
        if hs > as_:
            W[h] += 1; L[a] += 1
        elif as_ > hs:
            W[a] += 1; L[h] += 1
    teams = [t for t in set(W) | set(L)]
    return sorted(teams, key=lambda t: -(W[t] / (W[t] + L[t]) if (W[t] + L[t]) else 0))


def _ps_round_of(gid: str) -> str:
    """포스트시즌 gameId 접두(4자리 시리즈 코드)로 라운드 추정. 알 수 없으면 ''.
    KBO 코드: 와일드카드 4444 / 준PO 5555 / PO 6666 / 한국시리즈 7777 (관측 기반·가변).
    """
    pref = str(gid)[:4]
    return {"4444": "wc", "5555": "spo", "6666": "po", "7777": "ks"}.get(pref, "")


def postseason_games(all_games: list) -> dict:
    """비정규(포스트시즌) 경기를 라운드키→경기리스트로. 날짜순."""
    def _is_reg(g):
        gid = str(g.get("gameId", "")); d = str(g.get("gameDate", "")).replace("-", "")
        return len(gid) >= 8 and gid[:8] == d
    out = defaultdict(list)
    for g in all_games:
        if _is_reg(g) or str(g.get("gameId", ""))[:4] == "9999":   # 정규·올스타 제외
            continue
        rk = _ps_round_of(g.get("gameId", ""))
        if rk:
            out[rk].append(g)
    for rk in out:
        out[rk].sort(key=lambda g: (g.get("gameDate", ""), g.get("gameId", "")))
    return dict(out)


def series_state(games: list, higher: str, lower: str) -> dict:
    """해당 라운드의 '현재 스코어'(상위/하위 승수)와 종료 여부."""
    hw = lw = 0
    for g in games:
        if g.get("statusCode") not in ("RESULT", "ENDED") or g.get("cancel"):
            continue
        hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
        if hs is None or as_ is None or hs == as_:
            continue
        winner = g["homeTeamCode"] if hs > as_ else g["awayTeamCode"]
        if winner == higher:
            hw += 1
        elif winner == lower:
            lw += 1
    return {"higher_wins": hw, "lower_wins": lw}


def series_odds(p_hi_home: float, p_hi_away: float, fmt: str,
                hi_wins: int = 0, lo_wins: int = 0,
                n_sims: int = 50000, seed: int = 7) -> float:
    """현재 스코어(hi_wins·lo_wins)에서 상위시드가 '시리즈'를 이길 확률(몬테카를로).
    p_hi_home/away: 상위시드가 홈/원정일 때 그 경기 승리확률."""
    rng = np.random.default_rng(seed)
    pattern = HOME_PATTERN[fmt]
    if fmt == "wc":
        # 4위(상위) 1승/무면 진출, 5위 2승 필요. 둘 다 상위홈.
        # 이미 치른 수만큼 건너뛰고 남은 경기 시뮬.
        played = hi_wins + lo_wins
        hi_adv = 0
        for _ in range(n_sims):
            h, l = hi_wins, lo_wins
            for gi in range(played, 2):
                if rng.random() < p_hi_home:   # 상위 홈 승
                    h += 1
                else:
                    l += 1
                if h >= 1:          # 상위 1승이면 진출(무는 생략·근사)
                    break
                if l >= 2:
                    break
            hi_adv += 1 if h >= 1 else 0
        return hi_adv / n_sims
    need = WINS_NEEDED[fmt]
    played = hi_wins + lo_wins
    hi_adv = 0
    for _ in range(n_sims):
        h, l = hi_wins, lo_wins
        gi = played
        while h < need and l < need and gi < len(pattern):
            hi_home = pattern[gi] == 1
            p = p_hi_home if hi_home else p_hi_away
            if rng.random() < p:
                h += 1
            else:
                l += 1
            gi += 1
        hi_adv += 1 if h >= need else 0
    return hi_adv / n_sims


# ──────────────────────────────────────────────────────────────────────────
# 경기별 예측(기존 game_projection 모델 재사용) + 매치업 승률
# ──────────────────────────────────────────────────────────────────────────
def _context(reg_games: list, box):
    """game_projection과 동일한 '정규시즌 팀 강도' 컨텍스트를 구성(재사용)."""
    import game_projection as gp
    from boxscore import identify_rotation
    prior = gp.prior_team_rates(config.SEASON)
    rsg, lg = gp.team_offense(reg_games, prior=prior)
    gp_team, wins = defaultdict(int), defaultdict(int)
    for g in reg_games:
        if g.get("statusCode") in ("RESULT", "ENDED") and not g.get("cancel") \
                and g.get("homeTeamScore") is not None:
            h, a = g["homeTeamCode"], g["awayTeamCode"]; hs, as_ = g["homeTeamScore"], g["awayTeamScore"]
            gp_team[h] += 1; gp_team[a] += 1
            if hs > as_: wins[h] += 1
            elif as_ > hs: wins[a] += 1
    return {
        "rsg": rsg, "lg": lg, "ps": gp._pitcher_stats(box), "rotation": identify_rotation(box),
        "pfs": gp.park_factors(reg_games), "prior_ra": (prior["ra"] if prior else None),
        "gp_team": gp_team, "team_wpct": {t: wins[t] / gp_team[t] for t in gp_team if gp_team[t]},
        "wrc_by_p": gp.load_lineup_wrc()[0], "team_base": gp.load_lineup_wrc()[1],
        "starter_stuff": gp.load_starter_stuff(config.SEASON),
    }


def _project(ctx, box, day: str, games: list) -> list:
    """ctx로 특정 날짜(day)의 games를 game_projection._project_day에 태워 예측."""
    import game_projection as gp
    c = ctx
    return gp._project_day(day, games, box, c["rsg"], c["lg"], c["lg"], c["ps"],
                           c["rotation"], c["pfs"], c["wrc_by_p"], c["team_base"],
                           c["prior_ra"], c["gp_team"], c["team_wpct"], c["starter_stuff"])


def project_ps_games(ctx, box, games: list) -> list:
    """실제 포스트시즌 경기들(선발·구장 실제값)을 날짜별로 예측해 합친다."""
    out = []
    for d in sorted({g.get("gameDate") for g in games}):
        day_games = [g for g in games if g.get("gameDate") == d]
        out += _project(ctx, box, d, day_games)
    return out


# 같은 홈구장을 쓰는 팀쌍(홈 이점의 '구장 친숙도' 요소가 없음 → 홈 이점 감쇠).
#   KBO: LG·두산은 잠실 공동 홈.
SHARED_HOME = [frozenset({"LG", "OB"})]
HOME_DAMP = 0.5   # 공유구장 시리즈는 홈 이점(중립 대비 편차)을 절반으로


def matchup_winprob(ctx, box, higher: str, lower: str, shared_park: bool = False) -> tuple:
    """팀강도 기준(선발·구장 미정) 매치업 승률 → (상위홈 승률, 상위원정 승률).
    시리즈 몬테카를로 입력용. 미래 날짜 합성경기로 양 방향 승률을 모델에서 뽑는다.
    shared_park=True(잠실 공유 등)면 홈 이점의 '구장 친숙도' 요소가 없으므로, 홈/원정
    승률을 중립(둘의 평균) 쪽으로 HOME_DAMP만큼 당겨 홈 이점을 줄인다(과신 방지)."""
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()

    def synth(home, away):
        return {"gameId": f"PS{home}{away}", "gameDate": future, "homeTeamCode": home,
                "awayTeamCode": away, "statusCode": "BEFORE", "stadium": None,
                "homeTeamScore": None, "awayTeamScore": None}
    a = _project(ctx, box, future, [synth(higher, lower)])     # 상위 홈
    b = _project(ctx, box, future, [synth(lower, higher)])     # 상위 원정(하위 홈)
    p_hi_home = (a[0]["winHome"] / 100.0) if a else 0.5
    p_hi_away = (b[0]["winAway"] / 100.0) if b else 0.5        # b에서 상위팀은 원정
    if shared_park:
        neutral = (p_hi_home + p_hi_away) / 2.0                # 홈 효과가 상쇄된 '순수 실력차'
        p_hi_home = neutral + HOME_DAMP * (p_hi_home - neutral)
        p_hi_away = neutral + HOME_DAMP * (p_hi_away - neutral)
    return p_hi_home, p_hi_away


def series_decided(fmt: str, hw: int, lw: int) -> tuple:
    """(종료여부, 상위시드가 승자인가). 미결이면 (False, None)."""
    if fmt == "wc":
        if hw >= 1: return True, True       # 4위 1승이면 진출
        if lw >= 2: return True, False      # 5위 2승
        return False, None
    need = WINS_NEEDED[fmt]
    if hw >= need: return True, True
    if lw >= need: return True, False
    return False, None


def build_postseason(reg_games: list, all_games: list, box) -> dict:
    """가을야구 전체 상태(대진·시리즈 현황·경기별 예측·시리즈 승리확률) 조립."""
    rank = final_standings(reg_games)
    if len(rank) < 5:
        return None
    seeds = {i + 1: rank[i] for i in range(5)}
    nm = config.TEAM_NAMES
    # 포스트시즌 경기(비정규·비올스타). 라운드는 gameId 접두사 대신 '대진 시드 매치업'으로
    #   매칭한다 — 접두사는 라운드마다 달라 신뢰 불가(관측: WC=4444·준PO=3333 …).
    def _is_reg(g):
        gid = str(g.get("gameId", "")); d = str(g.get("gameDate", "")).replace("-", "")
        return len(gid) >= 8 and gid[:8] == d
    ps_all = [g for g in all_games if not _is_reg(g) and str(g.get("gameId", ""))[:4] != "9999"]
    ctx = _context(reg_games, box)
    prev_round = {"spo": "wc", "po": "spo", "ks": "po"}
    winners, rounds_out = {}, []
    for key, name, fmt, hi_seed, lo_seed in ROUNDS:
        higher = seeds[hi_seed]
        lower = seeds[lo_seed] if lo_seed is not None else winners.get(prev_round[key])
        # 이 라운드 경기 = 그 대진의 두 팀이 붙는 포스트시즌 경기(접두사 무관).
        games = ([g for g in ps_all
                  if {g.get("homeTeamCode"), g.get("awayTeamCode")} == {higher, lower}]
                 if lower else [])
        games.sort(key=lambda g: (g.get("gameDate", ""), g.get("gameId", "")))
        st = series_state(games, higher, lower) if lower else {"higher_wins": 0, "lower_wins": 0}
        hw, lw = st["higher_wins"], st["lower_wins"]
        decided, winner_hi = series_decided(fmt, hw, lw) if lower else (False, None)
        if decided and lower:
            winners[key] = higher if winner_hi else lower
        # 예보는 '완료된 경기(결과 표시) + 다음 한 경기'만. 아직 안 열린 이후 경기
        #   (선발 미정·열릴지도 불확실)까지 미리 예보하지 않는다.
        FIN = ("RESULT", "ENDED")
        show = [g for g in games if g.get("statusCode") in FIN]
        nxt = next((g for g in games
                    if g.get("statusCode") not in FIN and not g.get("cancel")), None)
        if nxt:
            show.append(nxt)
        preds = project_ps_games(ctx, box, show) if show else []
        odds = None
        if lower and not decided:
            shared = frozenset({higher, lower}) in SHARED_HOME   # 잠실 공유 등
            php, hpa = matchup_winprob(ctx, box, higher, lower, shared_park=shared)
            adv = series_odds(php, hpa, fmt, hi_wins=hw, lo_wins=lw)
            odds = {"higher_adv": round(adv * 100), "lower_adv": round((1 - adv) * 100),
                    "p_hi_home": round(php * 100), "p_hi_away": round(hpa * 100),
                    "shared_park": shared}
        rounds_out.append({
            "key": key, "name": name, "fmt": fmt,
            "higher": higher, "higherName": nm.get(higher, higher), "higherSeed": hi_seed,
            "lower": lower, "lowerName": (nm.get(lower, lower) if lower else None),
            "lowerSeed": (lo_seed if lo_seed is not None else None),
            "higherWins": hw, "lowerWins": lw, "decided": decided,
            "winner": (winners.get(key) if decided else None),
            "winnerName": (nm.get(winners[key], winners[key]) if key in winners else None),
            "games": preds, "odds": odds,
        })
    return {"generated": datetime.date.today().isoformat(),
            "seeds": {k: nm.get(v, v) for k, v in seeds.items()}, "rounds": rounds_out}
