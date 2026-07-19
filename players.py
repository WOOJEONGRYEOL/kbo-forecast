# -*- coding: utf-8 -*-
"""
players.py — 투수/타자 개인 평가 파이프라인 실행 진입점
=========================================================

팀 단위 예측(main.py)과 별도로, 선수 개개인을 평가합니다.

사용법:
    python players.py              # 2026 시즌 전체 투수/타자 평가

⏱️ 첫 실행 주의:
    시즌 전체 경기(400+)의 박스스코어를 하나씩 받아오므로
    첫 실행은 몇 분 걸립니다. 한 번 받은 경기는 data/box/에
    캐시되어 다음부터는 새 경기만 받아옵니다.

산출물:
    - 콘솔: 억울한 투수 / 시한폭탄 / 저평가·거품 타자 스크리닝
    - data/players.html : 산점도 4종 + 스크리닝 테이블 대시보드
    - data/pitchers_YYYY-MM-DD.csv, batters_YYYY-MM-DD.csv
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import config
import boxscore
import kbostuff_client
import naver_games
import player_eval
import player_dashboard


def _print_screen(title: str, df, cols: list[str], limit: int = 10) -> None:
    """스크리닝 결과 하나를 콘솔에 보기 좋게 출력합니다."""
    print(f"\n─── {title} " + "─" * max(0, 60 - len(title) * 2))
    if len(df) == 0:
        print("  (해당 선수 없음)")
        return
    print(df.head(limit)[cols].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="KBO 선수 개인 평가")
    parser.add_argument("--season", type=int, default=config.SEASON)
    args = parser.parse_args()

    # ── 1. 데이터 수집 ──
    print(f"\n[1/3] {args.season} 시즌 데이터 수집")
    games = naver_games.fetch_season_games(args.season)
    games = naver_games.filter_regular_season(games)  # 시범경기 제거
    games = naver_games.filter_official_teams(games)  # 올스타전 등 제외
    box = boxscore.collect_season_pitching(games)
    season_stats = boxscore.season_pitcher_stats(box)
    print(f"  → 투수 시즌 성적 {len(season_stats)}명 (박스스코어 합산)")

    stuff = kbostuff_client.fetch_pitching_metrics(args.season)
    bat_metrics = kbostuff_client.fetch_batter_metrics(args.season)
    bat_wrc = kbostuff_client.fetch_batter_wrc(args.season)
    fcb = kbostuff_client.fetch_fcb(args.season)
    print(f"  → kbostuff 지표: 투수 {len(stuff)}명 / 타자 {len(bat_metrics)}명 "
          f"/ FCB {len(fcb)}명")

    # ── 2. 평가 모델 ──
    print("\n[2/3] 평가 모델 계산")
    pitchers = player_eval.evaluate_pitchers(season_stats, stuff)
    batters = player_eval.evaluate_batters(bat_metrics, bat_wrc, fcb)
    p_screens = player_eval.pitcher_screens(pitchers)
    b_screens = player_eval.batter_screens(batters)
    lg_era = 9.0 * season_stats["er"].sum() / season_stats["ip"].sum()
    print(f"  → 평가 대상: 투수 {len(pitchers)}명(≥{player_eval.MIN_IP}이닝), "
          f"타자 {len(batters)}명(≥{player_eval.MIN_PA}타석), "
          f"리그 ERA {lg_era:.2f}")

    # ── 3. 리포트 ──
    print("\n[3/3] 리포트")

    pcols = ["name", "team_name", "ip", "era", "fip", "k_stuff_v2", "k_control_v2"]
    _print_screen("📈 억울한 투수 (구위 최상급인데 성적이 안 따라옴 → 반등 후보)",
                  p_screens["unlucky"], pcols)
    _print_screen("⚠️ 시한폭탄 (구위 평균 이하인데 ERA 좋음 → 하락 경계)",
                  p_screens["timebomb"], pcols)
    _print_screen("🛡️ 수비/운 피해자 (ERA − FIP > 0.7)",
                  p_screens["defense_victim"],
                  ["name", "team_name", "ip", "era", "fip", "era_fip_gap"])

    bcols = ["player_name", "team_name", "n_pa", "overall_plus",
             "babip", "luck", "wrc_plus_pure"]
    _print_screen("💎 저평가 타자 (BABIP 불운 → 곧 터질 후보)",
                  b_screens["undervalued"], bcols)
    _print_screen("🫧 거품 주의 타자 (BABIP 고평가 → 유지 어려움)",
                  b_screens["bubble"], bcols)
    _print_screen("🏟️ 구장에 갇힌 타자 (순수 wRC+ ≫ 이벤트 wRC+)",
                  b_screens["park_victim"],
                  ["player_name", "team_name", "n_pa",
                   "wrc_plus_pure", "wrc_plus_event", "park_gap"])
    _print_screen("🔥 승부처 강자 FCB (누적 승리기여 최상위 — 설명형 지표)",
                  b_screens["clutch"],
                  ["player_name", "team_name", "n_pa",
                   "wins_contributed", "avg_src_per_game", "overall_plus"])

    # CSV 저장 (엑셀 호환 인코딩)
    Path(config.DATA_DIR).mkdir(exist_ok=True)
    p_csv = Path(config.DATA_DIR) / f"pitchers_{date.today()}.csv"
    b_csv = Path(config.DATA_DIR) / f"batters_{date.today()}.csv"
    pitchers.to_csv(p_csv, index=False, encoding="utf-8-sig", float_format="%.3f")
    batters.to_csv(b_csv, index=False, encoding="utf-8-sig", float_format="%.3f")

    latest_game = box["date"].max() if len(box) else None
    html = player_dashboard.save_player_dashboard(
        pitchers, batters, p_screens, b_screens, lg_era, latest_game)

    print(f"\nCSV 저장 완료 → {p_csv}, {b_csv}")
    print(f"대시보드 저장 완료 → {html}")
    print(f"  (브라우저에서 열기: open {html})\n")


if __name__ == "__main__":
    main()
