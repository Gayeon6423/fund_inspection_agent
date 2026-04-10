import json
import logging
import os
import sys
import time
import traceback
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Optional

# agent/ 디렉토리를 sys.path에 추가 (uvicorn agent.api_server:app 형태로 실행 시 필요)
_AGENT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _AGENT_DIR.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv, find_dotenv
from excel_json.excel_to_json import convert_excel_to_json_by_sheets

load_dotenv(find_dotenv(), override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("fund-agent")

# ── 환경변수 ──────────────────────────────────────────────
api_key = os.getenv("ANTHROPIC_API_KEY")
system_prompt_version = os.getenv("SYSTEM_PROMPT_VERSION", "system_prompt_v5")
if not api_key:
    print("에러: ANTHROPIC_API_KEY 환경변수를 먼저 설정해주세요.")
    sys.exit(1)

MODEL        = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR   = PROJECT_ROOT / "data" / "output_agent"

_prompt_path = Path(__file__).resolve().parent / "prompt" / f"{system_prompt_version}.txt"
with open(_prompt_path, encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()

# ── fund_core import ──────────────────────────────────────
from fund_core import call_claude, parse_json_from_text

# ── FastAPI 앱 ────────────────────────────────────────────
app = FastAPI(
    title="Fund Agent API",
    version="1.0.0",
    description="Claude 기반 펀드 AI 에이전트 API",
)

# ── 요청 스키마 ───────────────────────────────────────────
class AskRequest(BaseModel):
    input_type: str = "text"
    user_query: Optional[str] = None
    file_path: Optional[str] = None
    script_file_path: Optional[str] = None   # .xlsx 또는 .json 모두 허용
    manual_file_path: Optional[str] = None
    sheet_name: Optional[str] = None          # xlsx일 때 시트 지정 (없으면 첫 번째 시트)


def log_step(request_id: str, step: str, detail: str = ""):
    suffix = f" | {detail}" if detail else ""
    logger.info(f"[{request_id}] {step}{suffix}")


def resolve_path(relative: str) -> Path:
    path = PROJECT_ROOT / relative
    if path.exists():
        return path
    matches = list((PROJECT_ROOT / "data").rglob(Path(relative).name))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail=f"동일 파일명이 여러 개입니다: {[str(m) for m in matches]}")
    raise HTTPException(status_code=400, detail=f"파일을 찾을 수 없습니다: {relative}")


def build_user_content(body: AskRequest, request_id: str) -> list:
    log_step(request_id, "입력 컨텐츠 구성 시작", f"input_type={body.input_type}")

    if body.input_type == "text":
        if not body.user_query:
            raise HTTPException(status_code=400, detail="text 모드에서는 user_query가 필요합니다.")
        return [{"type": "text", "text": body.user_query}]

    if body.input_type == "json_file":
        if not body.file_path:
            raise HTTPException(status_code=400, detail="json_file 모드에서는 file_path가 필요합니다.")
        path = resolve_path(body.file_path)
        log_step(request_id, "JSON 파일 로드", str(path))
        with path.open(encoding="utf-8") as f:
            content = json.load(f)
        return [{"type": "text", "text": json.dumps(content, ensure_ascii=False, indent=2)}]

    if body.input_type == "pdf_file":
        if not body.file_path:
            raise HTTPException(status_code=400, detail="pdf_file 모드에서는 file_path가 필요합니다.")
        import base64
        path = resolve_path(body.file_path)
        log_step(request_id, "PDF 파일 로드", str(path))
        with path.open("rb") as f:
            pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}}]

    if body.input_type == "compare":
        if not body.script_file_path or not body.manual_file_path:
            raise HTTPException(status_code=400, detail="compare 모드에서는 script_file_path와 manual_file_path가 필요합니다.")
        import base64

        script_path = resolve_path(body.script_file_path)
        log_step(request_id, "판매대본 로드", str(script_path))

        if script_path.suffix.lower() == ".xlsx":
            # xlsx → JSON 변환 (임시 디렉토리에 저장)
            output_excel_json_dir = PROJECT_ROOT / "data" / "output_excel_json"
            output_excel_json_dir.mkdir(parents=True, exist_ok=True)
            sheet_names = [body.sheet_name] if body.sheet_name else None
            conversions = convert_excel_to_json_by_sheets(
                input_path=script_path,
                sheet_names=sheet_names,
                output_dir=output_excel_json_dir,
            )
            if not conversions:
                raise HTTPException(status_code=400, detail="xlsx 변환 결과가 없습니다. sheet_name을 확인해주세요.")
            # sheet_name 미지정 시 첫 번째 시트 사용
            script_content = json.loads(Path(conversions[0]["output_path"]).read_text(encoding="utf-8"))
            log_step(request_id, "xlsx → JSON 변환 완료", conversions[0]["output_path"])
        else:
            with script_path.open(encoding="utf-8") as f:
                script_content = json.load(f)

        manual_path = resolve_path(body.manual_file_path)
        log_step(request_id, "설명서 PDF 로드", str(manual_path))
        with manual_path.open("rb") as f:
            pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

        return [
            {"type": "text", "text": f"[판매대본 JSON]\n{json.dumps(script_content, ensure_ascii=False, indent=2)}"},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
        ]

    raise HTTPException(status_code=400, detail=f"지원하지 않는 input_type: {body.input_type}")


# ── 엔드포인트 ────────────────────────────────────────────
@app.post("/v1/agent/fund/ask")
def ask(body: AskRequest):
    request_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    started_at = time.perf_counter()
    log_step(request_id, "요청 수신", f"input_type={body.input_type}")

    try:
        user_content = build_user_content(body, request_id)
        answer = call_claude(user_content, MODEL, api_key, SYSTEM_PROMPT)

        log_step(request_id, "응답 JSON 파싱 시작")
        log_step(request_id, "Claude 응답 미리보기", answer[:300].replace("\n", " "))
        try:
            parsed = parse_json_from_text(answer)
        except (ValueError, json.JSONDecodeError) as e:
            log_step(request_id, "JSON 파싱 실패 전체 응답", answer[:1000].replace("\n", " "))
            raise HTTPException(status_code=500, detail=f"JSON 파싱 실패: {e}")
        log_step(request_id, "응답 JSON 파싱 완료")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"local_{timestamp}_{system_prompt_version}.json"
        output_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        log_step(request_id, "결과 파일 저장 완료", str(output_path))

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_step(request_id, "요청 처리 완료", f"{elapsed_ms}ms")
        return parsed

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_step(request_id, "요청 처리 실패", f"{type(e).__name__}: {e} | {elapsed_ms}ms")
        raise HTTPException(status_code=500, detail=f"서버 오류: {type(e).__name__}: {e}")


# ── 헬스체크 ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}
