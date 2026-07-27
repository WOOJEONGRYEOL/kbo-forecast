#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
표준입력으로 받은 갱신 로그를 Gmail(SMTP)로 발송.

환경변수(없으면 조용히 스킵):
  STATIZ_MAIL_APP_PW = Gmail '앱 비밀번호'(16자리, 공백 제거)  ← 일반 비밀번호 아님
  STATIZ_MAIL_FROM   = 보내는 Gmail 주소 (기본 jeongryeolwoo@gmail.com)
  STATIZ_MAIL_TO     = 받는 주소 (기본 = FROM, 즉 나에게)

사용:
  cat output/cron.log | python send_log_email.py
"""
import os
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage

FROM = os.environ.get("STATIZ_MAIL_FROM", "jeongryeolwoo@gmail.com")
TO = os.environ.get("STATIZ_MAIL_TO", FROM)
PW = os.environ.get("STATIZ_MAIL_APP_PW", "").replace(" ", "")


def main():
    if not PW:
        print("[mail] STATIZ_MAIL_APP_PW 미설정 → 이메일 스킵")
        return 0
    body = sys.stdin.read().strip() or "(로그 내용 없음)"
    # 너무 길면 뒷부분(최근 로그)만
    if len(body) > 50000:
        body = "...(생략)...\n" + body[-50000:]

    msg = EmailMessage()
    msg["Subject"] = f"[statiz] 갱신 로그 {date.today():%Y-%m-%d}"
    msg["From"] = FROM
    msg["To"] = TO
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(FROM, PW)
            s.send_message(msg)
        print(f"[mail] 발송 완료 → {TO}")
    except Exception as e:
        print(f"[mail] 발송 실패: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
