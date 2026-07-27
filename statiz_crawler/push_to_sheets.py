#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → Google Sheets 업로드 (gspread + 서비스 계정).

환경변수(둘 다 있어야 동작, 없으면 조용히 스킵):
  GOOGLE_SERVICE_ACCOUNT_JSON = 서비스계정 키 파일 경로 (예: ~/.statiz_sa.json)
  STATIZ_GSHEET_ID            = 대상 스프레드시트 ID (URL 의 /d/<ID>/edit 부분)
  (대상 시트를 서비스계정 이메일에 '편집자'로 공유해 두어야 함)

사용법:
  python push_to_sheets.py                       # output/csv 전체(주의: 탭 많음)
  python push_to_sheets.py output/csv/batter_2026.csv ...   # 지정 파일만(권장, 매일 갱신용)

각 CSV 는 파일명(확장자 제외)을 워크시트(탭) 이름으로 하여 통째로 덮어쓴다.
"""
import glob
import os
import sys

import pandas as pd

SA = os.path.expanduser(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
SHEET_ID = os.environ.get("STATIZ_GSHEET_ID", "")


def main():
    if not SA or not SHEET_ID:
        print("[sheets] GOOGLE_SERVICE_ACCOUNT_JSON / STATIZ_GSHEET_ID 미설정 → 스킵")
        return 0
    if not os.path.exists(SA):
        print(f"[sheets] 서비스계정 파일 없음: {SA} → 스킵")
        return 0

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SA, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    files = sys.argv[1:] or sorted(glob.glob("output/csv/*.csv"))
    ok = 0
    for f in files:
        if not os.path.exists(f):
            continue  # 아직 안 받은 파일은 건너뜀
        name = os.path.splitext(os.path.basename(f))[0][:99]  # 탭 이름(최대 100자)
        df = pd.read_csv(f, dtype=str).fillna("")
        try:
            ws = sh.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=name, rows=len(df) + 10, cols=len(df.columns) + 2)
        ws.clear()
        data = [df.columns.tolist()] + df.values.tolist()
        ws.update(range_name="A1", values=data)
        print(f"[sheets] {name}: {df.shape[0]}행 {df.shape[1]}열 업로드")
        ok += 1
    print(f"[sheets] 총 {ok}개 워크시트 갱신 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
