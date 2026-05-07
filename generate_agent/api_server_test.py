import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)
HOST = "127.0.0.1"
PORT = int("8012")
BASE_URL = f"http://{HOST}:{PORT}"
APP_MODULE = "generate_agent.api_server:app"
ENDPOINT = "/v1/agent/fund/generate-script"


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


manual_file = os.getenv("GENERATE_MANUAL_FILE") 
if not manual_file:
    raise SystemExit("GENERATE_MANUAL_FILE 환경변수가 필요합니다.")
manual_file_path = f"data/uploads_local/{manual_file}"
product_name = os.getenv("GENERATE_MANUAL_FILE_NAME")
payload = {"manual_file_path": manual_file_path,
           "manual_file_name": product_name}

cmd = [sys.executable,"-m","uvicorn",APP_MODULE,"--host",HOST,"--port",str(PORT),"--log-level","warning"]
print("서버 실행:", " ".join(cmd))

if _is_port_open(HOST, PORT):
    raise SystemExit(
        f"{HOST}:{PORT} 포트를 이미 다른 프로세스가 사용 중입니다. "
        "기존 서버를 종료한 뒤 다시 실행해주세요. "
        "(예: lsof -nP -iTCP:8012 -sTCP:LISTEN)"
    )

child_env = os.environ.copy()
if APP_MODULE.startswith("generate_agent.") and not child_env.get("GENERATE_SYSTEM_PROMPT_VERSION"):
    child_env["GENERATE_SYSTEM_PROMPT_VERSION"] = "generate_system_prompt_v3"
print("테스트 프로세스 LLM_MODEL:", os.getenv("LLM_MODEL"))
print("서버 실행 환경 LLM_MODEL:", child_env.get("LLM_MODEL"))

proc = subprocess.Popen(
    cmd,
    cwd=str(PROJECT_ROOT),
    env=child_env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)

### 서버 헬스체크
health_url = f"{BASE_URL}/health"
started = time.time()
while time.time() - started < 30:
    if proc.poll() is not None:
        logs = proc.stdout.read() if proc.stdout is not None else ""
        raise RuntimeError(f"서버가 비정상 종료되었습니다.\n{logs}")
    try:
        health_resp = requests.get(health_url, timeout=3)
        if health_resp.ok:
            print("헬스체크 성공:", health_resp.json())
            break
        if health_resp.status_code >= 500:
            raise RuntimeError(
                f"헬스체크 실패: HTTP {health_resp.status_code}, body={health_resp.text[:500]}"
            )
    except requests.RequestException:
        pass
    time.sleep(0.5)
else:
    raise TimeoutError(f"서버 헬스체크 타임아웃: {health_url}")

### 서버에 요청: PDF 파일 경로와 상품명을 보내서 판매대본 생성
url = f"{BASE_URL}{ENDPOINT}"
print("요청 URL:", url)
print("요청 본문:", json.dumps(payload, ensure_ascii=False, indent=2))
resp = requests.post(url, json=payload, timeout=300)
try:
    print("응답 JSON:")
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
except ValueError:
    print("응답 원문:")
    print(resp.text)
