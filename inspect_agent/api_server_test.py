"""
로컬 Fund Agent API 테스트 클라이언트
서버를 먼저 실행한 뒤 이 파일을 실행하세요.
  > uvicorn inspect_agent.api_server:app --reload --port 8000
"""
import json
import os

import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=True)

BASE_URL = "http://localhost:8000"

# ── 입력 타입 선택 ────────────────────────────────────────
# "text"      : USER_QUERY 텍스트를 직접 전송
# "json_file" : INPUT_SCRIPT_FILE (판매대본 JSON) 파일을 읽어 전송
# "pdf_file"  : INPUT_MANUAL_FILE (제안서 PDF) 파일을 읽어 전송
# "compare"   : 두 파일을 비교 분석 (category, summary, match_rate, mismatches 반환)
INPUT_TYPE = "compare"

# ── 입력 타입에 따른 요청 데이터 구성 ─────────────────────
if INPUT_TYPE == "text":
    data = {
        "input_type": "text",
        "user_query": os.getenv("USER_QUERY"),
    }
elif INPUT_TYPE == "json_file":
    data = {
        "input_type": "json_file",
        "file_path": f"data/output_excel_json/{os.getenv('INPUT_SCRIPT_FILE')}",
    }
elif INPUT_TYPE == "pdf_file":
    data = {
        "input_type": "pdf_file",
        "file_path": f"data/uploads_local/{os.getenv('INPUT_MANUAL_FILE')}",
    }
elif INPUT_TYPE == "compare":
    data = {
        "input_type": "compare",
        "script_file_path": f"data/uploads_local/{os.getenv('INPUT_SCRIPT_FILE')}",
        "manual_file_path": f"data/uploads_local/{os.getenv('INPUT_MANUAL_FILE')}",
        "sheet_name": os.getenv("INPUT_SCRIPT_SHEET"),  # None이면 첫 번째 시트 사용
    }

# ── 요청 전송 및 결과 출력 ────────────────────────────────
url = f"{BASE_URL}/v1/agent/fund/ask"
print(f"요청 전송: {url}")
resp = requests.post(url, json=data, timeout=300)
print(f"HTTP 상태: {resp.status_code}")
if resp.ok:
    result = resp.json()
    print(json.dumps(result, ensure_ascii=False, indent=2))
else:
    print("오류:", resp.text)
