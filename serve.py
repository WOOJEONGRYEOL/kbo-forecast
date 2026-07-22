# -*- coding: utf-8 -*-
"""
serve.py — 대시보드 로컬 서버 + 수동 갱신 엔드포인트
=====================================================

대시보드는 자체완결 HTML이라 file:// 로도 열리지만, 그 경우 '갱신 버튼'은
작동하지 않습니다(브라우저는 로컬 파이썬을 실행할 수 없음). 이 서버를 통해
http://localhost:8799 로 열면, 대시보드의 🔄 버튼이 /refresh 를 호출해
파이프라인(main.py → players.py)을 돌리고 완료 후 자동 새로고침합니다.

바탕화면 런처(KBO 대시보드.app)가 이 서버를 띄웁니다. 직접 실행도 가능:
    .venv/bin/python serve.py
"""

import http.server
import json
import socketserver
import subprocess
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PORT = 8799
PY = ROOT / ".venv" / "bin" / "python"

# 갱신 상태 (여러 탭이 폴링해도 공유되는 단일 상태)
_lock = threading.Lock()
_state = {"status": "idle", "message": "", "updated": None}


def _run_pipeline() -> None:
    """main.py → players.py 를 순서대로 실행하고 상태를 갱신합니다."""
    try:
        for script in ("main.py", "players.py"):
            subprocess.run([str(PY), str(ROOT / script)],
                           cwd=str(ROOT), check=True)
        _state.update(status="done", message="",
                      updated=datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception as e:  # 파이프라인 실패를 버튼에 그대로 노출
        _state.update(status="error", message=str(e)[:200])


class Handler(http.server.SimpleHTTPRequestHandler):
    """data/ 를 정적 서빙하면서 /refresh · /status 를 추가로 처리."""

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DATA), **k)

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # HTML은 캐시하지 않아 새로고침 때 항상 최신 파일을 받게 합니다
        if self.path.endswith(".html"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if self.path == "/refresh":
            with _lock:
                if _state["status"] != "running":
                    _state.update(status="running", message="")
                    threading.Thread(target=_run_pipeline, daemon=True).start()
            self._json({"status": _state["status"]})
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/status":
            self._json(_state)
        else:
            super().do_GET()

    def log_message(self, *a):   # 콘솔을 조용히 (에러만 파이프라인 상태로)
        pass


def main() -> None:
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    # 127.0.0.1 바인드 — 외부에서 접근 불가 (로컬 전용)
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as srv:
        print(f"⚾  KBO 대시보드 서버 실행 중")
        print(f"    → http://localhost:{PORT}/dashboard.html  (팀 전력·순위)")
        print(f"    → http://localhost:{PORT}/players.html    (선수 평가)")
        print(f"    대시보드의 '🔄 지금 갱신' 버튼이 이제 작동합니다.")
        print(f"    (이 창을 닫으면 서버와 갱신 기능이 멈춥니다)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n서버를 종료합니다.")


if __name__ == "__main__":
    main()
