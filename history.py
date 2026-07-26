# -*- coding: utf-8 -*-
"""
history.py — 역대 탭 생성 진입점
=================================

data/history_*.csv (build_history.py 산출, 커밋됨)를 읽어 data/history.html 을
만든다. Statiz 역대 데이터는 자동 갱신이 아니므로 이 페이지는 스냅샷이다.

사용: python history.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import history_dashboard

if __name__ == "__main__":
    out = history_dashboard.save_history()
    print(f"역대 대시보드 저장 완료 → {out}")
