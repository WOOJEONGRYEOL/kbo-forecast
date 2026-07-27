# -*- coding: utf-8 -*-
"""
build_clusters.py — 선수 유형(아키타입) K-means 군집화
=======================================================

data/history_*.csv 의 스타일 피처로 선수-시즌을 비지도 군집화하고, 각 군집을
사람이 읽는 유형명으로 라벨링해 history_*.csv 에 'arch' 컬럼으로 되쓴다.

- 타자 피처: wRC+, ISO, K%, BB%, 도루율(SB/PA)
- 투수 피처: K/9, BB/9, HR/9, 역할(선발=1/불펜=0)

정적 사이트라 빌드 시점에 미리 계산. build_history.py 다음에 실행:
    python build_history.py && python build_clusters.py
"""

import csv
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

K_BAT, K_PIT = 6, 5
MIN_PA, MIN_IP = 200, 40   # 유형 학습용 표본 (안정적인 스타일만으로 fit)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def cluster(path, feat_cols, k, min_key, min_val, namer):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    def vec(r):
        return [_f(r[c]) for c in feat_cols]
    fit_rows = [r for r in rows if _f(r[min_key]) >= min_val]
    Xfit = np.array([vec(r) for r in fit_rows])
    sc = StandardScaler().fit(Xfit)
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(sc.transform(Xfit))
    # 군집 중심(원 단위) → 사람이 읽는 이름
    cen = sc.inverse_transform(km.cluster_centers_)
    names = namer(cen, feat_cols)
    # 전체 선수-시즌에 유형 예측 (표본 미달도 포함)
    Xall = sc.transform(np.array([vec(r) for r in rows]))
    labels = km.predict(Xall)
    for r, lab in zip(rows, labels):
        r["arch"] = names[lab]
    # 되쓰기
    cols = list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)
    # 진단: 중심 프로필
    print(f"\n{Path(path).name} — {k}개 유형")
    for i in range(k):
        prof = "  ".join(f"{c} {cen[i][j]:.2f}" for j, c in enumerate(feat_cols))
        n = int((labels == i).sum())
        print(f"  [{names[i]:14}] n={n:5}  {prof}")
    return names


def name_bat(cen, cols):
    # cols = [wrcplus, iso, kpct, bbpct, sbrate] — 우선순위로 유일 매핑
    W, I, K, B, S = (cols.index(c) for c in ["wrcplus", "iso", "kpct", "bbpct", "sbrate"])
    out = []
    for c in cen:
        wrc, iso, kk, bb, sb = c[W], c[I], c[K], c[B], c[S]
        if sb >= 5:
            out.append("🏃 호타준족")
        elif wrc >= 135 and iso >= 0.19:
            out.append("💪 슬러거")
        elif wrc < 82:
            out.append("🧤 수비형·백업")
        elif kk >= 19:
            out.append("🎰 스윙형(삼진↑)")
        elif bb >= 11:
            out.append("👁 출루형")
        else:
            out.append("🎯 정교한 타자")
    return _dedupe(out)


def name_pit(cen, cols):
    # cols = [k9, bb9, hr9, is_starter] — 우선순위로 유일 매핑
    K, B, H, R = (cols.index(c) for c in ["k9", "bb9", "hr9", "is_starter"])
    out = []
    for c in cen:
        k9, bb9, hr9, st = c[K], c[B], c[H], c[R]
        if k9 >= 8.5:
            out.append("🔥 파워피처")
        elif bb9 >= 4.5:
            out.append("🎲 제구난")
        elif hr9 >= 1.2:
            out.append("💣 피홈런형")
        elif st >= 0.6:
            out.append("🧱 이닝이터")
        else:
            out.append("🎯 맞춰잡는 불펜")
    return _dedupe(out)


def _dedupe(names):
    """같은 이름이 겹치면 번호를 붙여 유일하게."""
    seen = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


def main():
    D = "data"
    cluster(f"{D}/history_batters.csv",
            ["wrcplus", "iso", "kpct", "bbpct", "sbrate"], K_BAT,
            "pa", MIN_PA, name_bat)
    # 투수: is_starter 파생 (gs>=10)
    pit_path = f"{D}/history_pitchers.csv"
    rows = list(csv.DictReader(open(pit_path, encoding="utf-8-sig")))
    for r in rows:
        r["is_starter"] = 1 if int(_f(r["gs"])) >= 10 else 0
    with open(pit_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    cluster(pit_path, ["k9", "bb9", "hr9", "is_starter"], K_PIT,
            "ip", MIN_IP, name_pit)


if __name__ == "__main__":
    main()
