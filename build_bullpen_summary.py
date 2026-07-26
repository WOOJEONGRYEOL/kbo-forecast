# -*- coding: utf-8 -*-
"""
build_bullpen_summary.py — Statiz 투수 데이터 → 팀 불펜 요약(data/bullpen_YYYY.csv)
====================================================================================

순위 카드의 ▲저평가/▼고평가 태그에 '불펜 회귀' 신호를 붙이기 위한 재료를
만든다. 팀 불펜(순수 릴리버, GS=0)의 IP 가중 ERA·FIP 괴리(ERA−FIP)를 계산.
  · ERA ≪ FIP (음수)  = 불펜이 실력보다 잘 막음 → 운 → 회귀 경계
  · ERA ≫ FIP (양수)  = 불펜이 실력보다 못 막음 → 불운 → 반등 여지

[왜 별도 요약 파일인가]
  Statiz 원본 CSV는 리포 밖(외부 크롤러 출력)에 있어 GitHub Actions가 못 본다.
  그래서 팀 10행짜리 요약만 data/ 에 커밋해 두고, 파이프라인은 이 요약을
  읽는다. Statiz는 자동 갱신이 아니므로 이 요약은 크롤 시점 스냅샷이다.

[팀 식별 — TeamColor]
  Statiz는 팀을 '배경색 있는 텍스트 span'으로 렌더하는데, 텍스트 약자는
  KIA·KT가 둘 다 'K'로 겹친다. 크롤러 패치로 보존한 배경색(TeamColor)이
  팀·프랜차이즈별로 고유하므로 이를 1차 키로 쓴다. (statiz_crawl.py 참고)

사용:
    python build_bullpen_summary.py                 # 기본 경로·2026
    python build_bullpen_summary.py --season 2026 --src /path/to/csv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

# Statiz 팀 배경색(hex) → 프로젝트 팀코드.
# 색이 텍스트 약자보다 신뢰도 높은 식별자다(약자 'K'는 KIA/KT 충돌).
COLOR_TO_CODE = {
    "#ed1c24": "HT",   # KIA (빨강)
    "#000000": "KT",   # KT (검정)
    "#fc1cad": "LG",   # LG (마젠타)
    "#002b69": "NC",   # NC (네이비)
    "#cf152d": "SK",   # SSG (레드)
    "#042071": "OB",   # 두산 (네이비)
    "#888888": "LT",   # 롯데 (그레이)
    "#0061aa": "SS",   # 삼성 (블루)
    "#86001f": "WO",   # 키움 (버건디)
    "#f37321": "HH",   # 한화 (오렌지)
}

DEFAULT_SRC = "/Users/woo/Horse/statiz_crawler/output/csv"


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build(season: int, src: str) -> Path:
    path = Path(src) / f"pitcher_{season}_all.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    if not rows or "TeamColor" not in rows[0]:
        raise SystemExit(
            f"{path}: TeamColor 컬럼이 없습니다 — 패치된 크롤러로 재크롤 필요")

    # 팀별 불펜(GS=0, 순수 릴리버) IP 가중 ERA·FIP
    agg = defaultdict(lambda: {"ip": 0.0, "er_w": 0.0, "fip_w": 0.0, "n": 0})
    unmapped = set()
    for r in rows:
        code = COLOR_TO_CODE.get((r.get("TeamColor") or "").lower())
        if not code:
            if r.get("TeamColor"):
                unmapped.add(r["TeamColor"])
            continue
        if _num(r["GS"]) > 0:          # 선발 제외 → 순수 불펜
            continue
        ip = _num(r["IP"])
        if ip <= 0:
            continue
        a = agg[code]
        a["ip"] += ip
        a["er_w"] += _num(r["ERA"]) * ip
        a["fip_w"] += _num(r["FIP"]) * ip
        a["n"] += 1

    if unmapped:
        print(f"⚠️ 미매핑 TeamColor(색 팔레트 변경?): {sorted(unmapped)}")

    out = Path("data") / f"bullpen_{season}.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["team", "n_relievers", "bullpen_ip",
                    "bullpen_era", "bullpen_fip", "bullpen_gap"])
        for code, a in sorted(agg.items()):
            era = a["er_w"] / a["ip"]
            fip = a["fip_w"] / a["ip"]
            w.writerow([code, a["n"], round(a["ip"], 1),
                        round(era, 2), round(fip, 2), round(era - fip, 2)])
    print(f"저장: {out} ({len(agg)}팀)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--src", default=DEFAULT_SRC)
    args = ap.parse_args()
    build(args.season, args.src)
