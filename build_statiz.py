# -*- coding: utf-8 -*-
"""
build_statiz.py — Statiz 원본 CSV → 대시보드용 배포 요약(data/*.csv)
===================================================================

Statiz 원본은 리포 밖(외부 크롤러 출력)이라 GitHub Actions가 못 본다. 그래서
필요한 열만 추린 요약을 data/ 에 커밋해 두고, 파이프라인은 이 요약을 읽는다.
Statiz는 자동 갱신이 아니므로 이 요약들은 '크롤 시점 스냅샷'이다.

산출:
  data/bullpen_{yy}.csv          팀 불펜(구원 등판>선발) IP가중 ERA·FIP 괴리 (순위 카드용)
  data/statiz_relievers_{yy}.csv 불펜 개인 (세이브·홀드·릴리프 WAR — 불펜 카드용)
  data/statiz_batters_{yy}.csv   타자 oWAR/dWAR (공격×수비 사분면용)

[팀 식별 — TeamColor]
  Statiz는 팀을 '배경색 있는 텍스트 span'으로 렌더하는데 텍스트 약자는 KIA·KT가
  둘 다 'K'로 겹친다. 크롤러 패치로 보존한 배경색(TeamColor)이 팀·프랜차이즈별로
  고유하므로 이를 1차 키로 쓴다. (statiz_crawl.py 참고)

사용:
    python build_statiz.py                       # 기본 경로·2026
    python build_statiz.py --season 2026 --src /path/to/csv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

# Statiz 팀 배경색(hex) → 프로젝트 팀코드 (약자 'K'는 KIA/KT 충돌 → 색으로 구분)
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
MIN_PA = 100     # 타자 사분면 최소 타석 (저표본 WAR·dWAR 노이즈 컷)
MIN_RELIEF_IP = 5  # 불펜 카드 최소 이닝


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _load(src: str, kind: str, season: int) -> list:
    path = Path(src) / f"{kind}_{season}_all.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    if not rows or "TeamColor" not in rows[0]:
        raise SystemExit(
            f"{path}: TeamColor 컬럼이 없습니다 — 패치된 크롤러로 재크롤 필요")
    return rows


def _code(r) -> str:
    return COLOR_TO_CODE.get((r.get("TeamColor") or "").lower(), "")


def _is_reliever(r) -> bool:
    """구원 등판(GR)이 선발 등판(GS)보다 많으면 불펜으로 본다.
    임시선발 1~2번 한 마무리·셋업(예: 두산 이영하 GS1/GR34/14S)도 포함,
    순수 선발·스윙 선발형만 제외. (KBO도 구원 등판이 더 많으면 구원으로 취급)"""
    return _num(r.get("GR", 0)) > _num(r.get("GS", 0))


def build_bullpen_team(pit: list, season: int) -> None:
    """팀 불펜 IP가중 ERA·FIP 괴리 (순위 카드 회귀 신호용)."""
    agg = defaultdict(lambda: {"ip": 0.0, "er_w": 0.0, "fip_w": 0.0, "n": 0})
    for r in pit:
        code = _code(r)
        if not code or not _is_reliever(r):    # 구원 등판이 더 많아야 불펜
            continue
        ip = _num(r["IP"])
        if ip <= 0:
            continue
        a = agg[code]
        a["ip"] += ip
        a["er_w"] += _num(r["ERA"]) * ip
        a["fip_w"] += _num(r["FIP"]) * ip
        a["n"] += 1
    out = Path("data") / f"bullpen_{season}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["team", "n_relievers", "bullpen_ip",
                    "bullpen_era", "bullpen_fip", "bullpen_gap"])
        for code, a in sorted(agg.items()):
            era, fip = a["er_w"] / a["ip"], a["fip_w"] / a["ip"]
            w.writerow([code, a["n"], round(a["ip"], 1),
                        round(era, 2), round(fip, 2), round(era - fip, 2)])
    print(f"저장: {out} ({len(agg)}팀)")


def build_relievers(pit: list, season: int) -> None:
    """불펜 개인 (세이브·홀드·릴리프 WAR·K-BB% — 불펜 카드용)."""
    out = Path("data") / f"statiz_relievers_{season}.csv"
    n = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "team", "war", "era", "fip", "ip",
                    "g", "gf", "sv", "hd", "k9", "bb9", "kbb"])
        for r in pit:
            code = _code(r)
            if not code or not _is_reliever(r):   # 구원 등판이 선발보다 많으면 불펜
                continue
            ip = _num(r["IP"])
            if ip < MIN_RELIEF_IP:
                continue
            so, bb, tbf = _num(r["SO"]), _num(r["BB"]), _num(r["TBF"])
            k9 = round(so * 9 / ip, 1) if ip else 0
            bb9 = round(bb * 9 / ip, 1) if ip else 0
            # K-BB% = (탈삼진 − 볼넷) / 상대타자. 투수 실력 예측력이 가장 높은 축.
            kbb = round((so - bb) / tbf * 100, 1) if tbf else 0
            w.writerow([r["Name"], code, _num(r["WAR"]), _num(r["ERA"]),
                        _num(r["FIP"]), ip, int(_num(r["G"])),
                        int(_num(r["GF"])), int(_num(r["S"])), int(_num(r["HD"])),
                        k9, bb9, kbb])
            n += 1
    print(f"저장: {out} ({n}명)")


def build_batters(bat: list, season: int) -> None:
    """타자 oWAR/dWAR + 규율(BB%·K%)·주루(SB·GDP) (사분면·규율용). PA>=100."""
    out = Path("data") / f"statiz_batters_{season}.csv"
    n = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "team", "pos", "owar", "dwar", "war",
                    "pa", "wrcplus", "bbpct", "kpct", "iso", "sb", "cs", "gdp"])
        for r in bat:
            code = _code(r)
            pa = _num(r["PA"])
            if not code or pa < MIN_PA:
                continue
            bbpct = round(_num(r["BB"]) / pa * 100, 1) if pa else 0
            kpct = round(_num(r["SO"]) / pa * 100, 1) if pa else 0
            iso = round(_num(r["SLG"]) - _num(r["AVG"]), 3)
            w.writerow([r["Name"], code, r.get("Pos", ""), _num(r["oWAR"]),
                        _num(r["dWAR"]), _num(r["WAR"]), int(pa),
                        _num(r.get("wRC+", 0)), bbpct, kpct, iso,
                        int(_num(r["SB"])), int(_num(r["CS"])), int(_num(r["GDP"]))])
            n += 1
    print(f"저장: {out} ({n}명, PA>={MIN_PA})")


def build(season: int, src: str) -> None:
    Path("data").mkdir(exist_ok=True)
    pit = _load(src, "pitcher", season)
    bat = _load(src, "batter", season)
    build_bullpen_team(pit, season)
    build_relievers(pit, season)
    build_batters(bat, season)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--src", default=DEFAULT_SRC)
    args = ap.parse_args()
    build(args.season, args.src)
