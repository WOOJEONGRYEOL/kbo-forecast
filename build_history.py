# -*- coding: utf-8 -*-
"""
build_history.py — 전 시즌 Statiz _all → 역대 탭용 distill(data/history_*.csv)
=============================================================================

1982~현재 모든 시즌의 선수-시즌 기록을 추려 역대 탭(시즌·포지션 순위 / 통산
GOAT)에 쓴다. 각 행 = 한 선수의 한 시즌. 원본은 리포 밖이라 요약만 커밋한다.

[팀 표기]
  Team 텍스트("95해3B" = 95년 해태 3루)는 그 시대의 약자라 그대로 쓰면
  시대 정확하다. 앞 연도·뒤 포지션을 떼어 약자(해/현/삼…)를 뽑고, 색(TeamColor)
  으로 함께 구분한다. 'K'만 KIA·KT가 겹쳐 색으로 가른다.

[통산 GOAT 주의]
  현재 파일엔 선수 고유 ID(PlayerNo)가 없어(크롤러 p_no 패치는 다음 재크롤부터)
  통산 집계를 '이름'으로 한다 → 동명이인이 뭉칠 수 있다. p_no 재크롤 후 정확해짐.

사용: python build_history.py [--src /path/to/csv]
"""

import argparse
import csv
import glob
import re
from pathlib import Path

DEFAULT_SRC = "/Users/woo/Horse/statiz_crawler/output/csv"
POS_RE = re.compile(r"(1B|2B|3B|SS|LF|CF|RF|OF|IF|DH|C|P|R)$")


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _abbr(team_text: str, color: str) -> str:
    """Team 텍스트 → 시대 약자. 'K'(KIA/KT)는 색으로 구분."""
    s = re.sub(r"^\d+\+?", "", team_text or "")   # 연도 프리픽스 제거
    s = POS_RE.sub("", s)                          # 포지션 접미 제거
    if s == "K":
        return "KIA" if (color or "").lower() == "#ed1c24" else "KT"
    return s or "?"


def _season(fname: str) -> int:
    m = re.search(r"_(\d{4})_all", fname)
    return int(m.group(1)) if m else 0


def build(src: str) -> None:
    Path("data").mkdir(exist_ok=True)

    # ── 타자 ──
    bout = Path("data") / "history_batters.csv"
    nb = 0
    with open(bout, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["season", "name", "team", "color", "pos",
                    "war", "owar", "dwar", "wrcplus", "pa", "hr", "ops"])
        for path in sorted(glob.glob(f"{src}/batter_*_all.csv")):
            yr = _season(path)
            if not yr:
                continue
            for r in csv.DictReader(open(path, encoding="utf-8-sig")):
                color = (r.get("TeamColor") or "").lower()
                w.writerow([yr, r["Name"], _abbr(r.get("Team", ""), color),
                            color, r.get("Pos", ""), _num(r["WAR"]),
                            _num(r["oWAR"]), _num(r["dWAR"]),
                            _num(r.get("wRC+", 0)), int(_num(r["PA"])),
                            int(_num(r["HR"])), _num(r.get("OPS", 0))])
                nb += 1
    print(f"저장: {bout} ({nb}행)")

    # ── 투수 ──
    pout = Path("data") / "history_pitchers.csv"
    npi = 0
    with open(pout, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["season", "name", "team", "color", "role",
                    "war", "era", "fip", "ip", "so", "gs"])
        for path in sorted(glob.glob(f"{src}/pitcher_*_all.csv")):
            yr = _season(path)
            if not yr:
                continue
            for r in csv.DictReader(open(path, encoding="utf-8-sig")):
                color = (r.get("TeamColor") or "").lower()
                gs = int(_num(r["GS"]))
                role = "선발" if gs >= 10 else "불펜" if gs == 0 else "스윙"
                w.writerow([yr, r["Name"], _abbr(r.get("Team", ""), color),
                            color, role, _num(r["WAR"]), _num(r["ERA"]),
                            _num(r["FIP"]), _num(r["IP"]), int(_num(r["SO"])),
                            gs])
                npi += 1
    print(f"저장: {pout} ({npi}행)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    build(ap.parse_args().src)
