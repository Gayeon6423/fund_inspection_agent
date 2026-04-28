"""
inspect_agent/api_server.py 통합 테스트 스크립트

기능:
1) FastAPI 서버 자동 실행
2) /health 확인
3) ask endpoint 호출
4) 응답 출력
5) 서버 자동 종료

실행:
  python inspect_agent/api_server_test.py
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(), override=True)

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent
HOST = os.getenv("TEST_HOST", "127.0.0.1")
PORT = int(os.getenv("TEST_PORT", "8000"))
BASE_URL = f"http://{HOST}:{PORT}"
APP_MODULE = os.getenv("TEST_APP_MODULE", "inspect_agent.api_server:app")
ENDPOINT = os.getenv("TEST_ENDPOINT", "/v1/agent/fund/ask")
INPUT_TYPE = os.getenv("TEST_INPUT_TYPE", "compare")


def build_payload() -> dict:
    if INPUT_TYPE == "text":
        user_query = os.getenv("USER_QUERY")
        if not user_query:
            raise ValueError("text 모드에서는 USER_QUERY 환경변수가 필요합니다.")
        return {"input_type": "text", "user_query": user_query}

    if INPUT_TYPE == "json_file":
        script_file = os.getenv("INPUT_SCRIPT_FILE")
        if not script_file:
            raise ValueError("json_file 모드에서는 INPUT_SCRIPT_FILE 환경변수가 필요합니다.")
        return {
            "input_type": "json_file",
            "file_path": script_file if script_file.startswith("data/") else f"data/output_excel_json/{script_file}",
        }

    if INPUT_TYPE == "pdf_file":
        manual_file = os.getenv("INPUT_MANUAL_FILE")
        if not manual_file:
            raise ValueError("pdf_file 모드에서는 INPUT_MANUAL_FILE 환경변수가 필요합니다.")
        return {
            "input_type": "pdf_file",
            "file_path": manual_file if manual_file.startswith("data/") else f"data/uploads_local/{manual_file}",
        }

    if INPUT_TYPE == "compare":
        script_file = os.getenv("INPUT_SCRIPT_FILE")
        manual_file = os.getenv("INPUT_MANUAL_FILE")
        if not script_file or not manual_file:
            raise ValueError("compare 모드에서는 INPUT_SCRIPT_FILE, INPUT_MANUAL_FILE 환경변수가 필요합니다.")
        return {
            "input_type": "compare",
            "script_file_path": script_file if script_file.startswith("data/") else f"data/uploads_local/{script_file}",
            "manual_file_path": manual_file if manual_file.startswith("data/") else f"data/uploads_local/{manual_file}",
            "sheet_name": os.getenv("INPUT_SCRIPT_SHEET"),
        }

    raise ValueError(f"지원하지 않는 TEST_INPUT_TYPE: {INPUT_TYPE}")


if __name__ == "__main__":
    payload = build_payload()

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        APP_MODULE,
        "--host",
        HOST,
        "--port",
        str(PORT),
        "--log-level",
        "warning",
    ]
    print("서버 실행:", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    exit_code = 1
    try:
        health_url = f"{BASE_URL}/health"
        started = time.time()
        while time.time() - started < 30:
            if proc.poll() is not None:
                logs = proc.stdout.read() if proc.stdout is not None else ""
                raise RuntimeError(f"서버가 비정상 종료되었습니다.\\n{logs}")
            try:
                health_resp = requests.get(health_url, timeout=3)
                if health_resp.ok:
                    print("헬스체크 성공:", health_resp.json())
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            raise TimeoutError(f"서버 헬스체크 타임아웃: {health_url}")

        url = f"{BASE_URL}{ENDPOINT}"
        print("요청 전송:", url)
        print("요청 본문:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        resp = requests.post(url, json=payload, timeout=300)
        print("HTTP 상태:", resp.status_code)

        try:
            print("응답 JSON:")
            print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
        except ValueError:
            print("응답 원문:")
            print(resp.text)

        exit_code = 0 if resp.ok else 1

    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    raise SystemExit(exit_code)
