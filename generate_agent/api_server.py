import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent # generate_agent/
PROJECT_ROOT = MODULE_DIR.parent # 펀드 불완전판매/
print(f"MODULE_DIR: {MODULE_DIR}")
print(f"PROJECT_ROOT: {PROJECT_ROOT}")

PROMPT_DIR = MODULE_DIR / "prompt"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output_generate_agent"
LOG_DIR = PROJECT_ROOT / "data" / "log"

load_dotenv(find_dotenv(), override=True)

API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
SYSTEM_PROMPT_VERSION = os.getenv("GENERATE_SYSTEM_PROMPT_VERSION", "generate_system_prompt_v3")


class DailyFileHandler(logging.Handler):
    def __init__(self, log_dir: Path, prefix: str):
        super().__init__()
        self.log_dir = log_dir
        self.prefix = prefix
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = ""
        self._stream = None

    def _ensure_stream(self):
        current_date = datetime.now().strftime("%Y-%m-%d")
        if self._stream is not None and self._current_date == current_date:
            return
        if self._stream is not None:
            self._stream.close()
        self._current_date = current_date
        self._stream = (self.log_dir / f"{self.prefix}_{self._current_date}.log").open("a", encoding="utf-8")

    def emit(self, record):
        try:
            self._ensure_stream()
            if self._stream is None:
                return
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("generate-agent")
logger.setLevel(logging.INFO)
logger.propagate = False
if not any(isinstance(handler, DailyFileHandler) for handler in logger.handlers):
    daily_file_handler = DailyFileHandler(LOG_DIR, prefix="generate")
    daily_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(daily_file_handler)
(LOG_DIR / f"generate_{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.log").touch(exist_ok=True)


class GenerateRequest(BaseModel):
    manual_file_path: str
    manual_file_name: Optional[str] = None


def _safe_tag(text: str) -> str:
    """
    파일명 등에서 안전하게 사용할 수 있도록 특수문자를 언더스코어로 변환
    """
    return re.sub(r'[\\/:*?"<>|]', "_", text).strip()


def _resolve_prompt_path() -> Path:
    """
    SYSTEM_PROMPT_VERSION에 해당하는 프롬프트 파일 경로를 반환합니다. .md를 우선하고 .txt를 fallback으로 시도합니다.
    """
    candidates = [
        PROMPT_DIR / f"{SYSTEM_PROMPT_VERSION}.md",
        PROMPT_DIR / f"{SYSTEM_PROMPT_VERSION}.txt",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"프롬프트 파일을 찾을 수 없습니다. 확인 대상: {', '.join(str(p) for p in candidates)}"
    )


def _load_system_prompt() -> str:
    """
    SYSTEM_PROMPT_VERSION에 해당하는 프롬프트 파일의 내용을 문자열로 일거옵니다.
    """
    return _resolve_prompt_path().read_text(encoding="utf-8").strip()


def _resolve_path(user_path: str) -> Path:
    """
    사용자가 준 파일 경로를 실제 파일로 찾아줍니다. 절대 경로, 프로젝트 루트 기준 상대 경로, 그리고 data/ 폴더 내에서 이름으로 검색하는 방식을 시도합니다.
    """
    raw = Path(user_path).expanduser()

    candidates = [raw]
    if not raw.is_absolute():
        candidates.append(PROJECT_ROOT / raw)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    data_root = PROJECT_ROOT / "data"
    matches = list(data_root.rglob(raw.name)) if data_root.exists() else []
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"동일 파일명이 여러 개입니다. 경로를 구체적으로 지정해주세요: {[str(m) for m in matches]}",
        )
    raise HTTPException(status_code=400, detail=f"파일을 찾을 수 없습니다: {user_path}")


def _extract_json_block(answer_text: str) -> str:
    """
    LLM 응답에서 JSON 블록을 추출합니다.
    """
    patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
    ]

    candidates = []
    for pattern in patterns:
        candidates.extend(re.findall(pattern, answer_text, flags=re.DOTALL))
    candidates.append(answer_text)

    for text in candidates:
        start = text.find("{")
        if start == -1:
            continue

        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]

    raise ValueError("LLM 응답에서 JSON 객체를 찾지 못했습니다.")


def _parse_json_from_text(answer_text: str) -> dict:
    """
    위 함수로 추출한 JSON 블록을 실제 JSON 객체인 파이썬 딕셔너리로 파싱합니다.
    """
    json_text = _extract_json_block(answer_text)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as first_error:
        # 모델이 스마트 따옴표/후행 쉼표를 섞어 내보내는 경우를 보정해 한 번 더 파싱 시도합니다.
        repaired = (
            json_text.replace("“", '"')
            .replace("”", '"')
            .replace("’", "'")
            .replace("‘", "'")
        )
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        if repaired != json_text:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        lines = json_text.splitlines()
        bad_line = lines[first_error.lineno - 1] if 1 <= first_error.lineno <= len(lines) else ""
        raise ValueError(
            f"LLM JSON 파싱 실패: {first_error.msg} (line {first_error.lineno}, column {first_error.colno}). "
            f"문제 줄: {bad_line[:200]}"
        ) from first_error


def _call_llm_generate(manual_pdf_bytes: bytes, manual_file_name: Optional[str]) -> str:
    """
    PDF와 추가 지시사항을 LLM API에 보내서 판매대본을 생성하고, 그 응답 텍스트를 반환합니다.
    """
    if not API_KEY:
        # API_KEY가 없으면 LLM API를 호출할 수 없으므로 예외를 발생시킵니다.
        raise HTTPException(status_code=500, detail="API_KEY 환경변수가 설정되어 있지 않습니다.")

    user_text = [
        "다음 상품설명서 PDF를 기반으로 판매대본을 생성해줘.",
    ]
    if manual_file_name:
        user_text.append(f"파일명: {manual_file_name}")
    # PDF 바이트를 base64로 인코딩해서 document 블록에 포함합니다.
    user_content = [
        {"type": "text", "text": "\n".join(user_text)},
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(manual_pdf_bytes).decode("utf-8"),
            },
        },
    ]

    payload = {
        "model": MODEL,
        "max_tokens": 8000,
        "system": _load_system_prompt(),
        "messages": [{"role": "user", "content": user_content}],
    }
    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "pdfs-2024-09-25",
        "content-type": "application/json",
    }
    # Anthropic API로 HTTP POST 전송
    req = urllib.request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=500, detail=f"LLM API 오류: {detail}") from e

    return result["content"][0]["text"]


app = FastAPI(
    title="Fund Script Generator API",
    version="0.1.0",
    description="상품설명서 PDF 기반 판매대본 생성 API",
)


@app.on_event("startup")
def on_startup():
    logger.info(
        "generate API 서버 시작 | model=%s | prompt_version=%s",
        MODEL,
        SYSTEM_PROMPT_VERSION,
    )

# 서버 상태 점검용 API
@app.get("/health")
def health():
    logger.info("health 체크 요청 수신")
    return {
        "status": "ok", # 서버 정상 여부
        "model": MODEL, # 현재 사용할 모델명
        "prompt_version": SYSTEM_PROMPT_VERSION, # 현재 프롬프트 버전
        "prompt_exists": _resolve_prompt_path().exists() if PROMPT_DIR.exists() else False, # 프롬프트 파일 존재 여부
    }

# 메인 API 엔드포인트: 상품설명서 PDF를 받아서 판매대본을 생성
@app.post("/v1/agent/fund/generate-script")
def generate_script(body: GenerateRequest):
    """
    상품설명서 PDF를 기반으로 판매대본을 생성합니다.
    """
    request_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    started_at = time.perf_counter()
    logger.info("[%s] generate 요청 수신 | manual_file_path=%s", request_id, body.manual_file_path)

    try:
        manual_path = _resolve_path(body.manual_file_path) # 파일 경로 해석
        logger.info("[%s] 파일 경로 확인 완료 | resolved_path=%s", request_id, manual_path)
        manual_pdf_bytes = manual_path.read_bytes() # PDF 파일을 바이너리로 읽기
        logger.info("[%s] 파일 읽기 완료 | size=%d bytes", request_id, len(manual_pdf_bytes))

        if not manual_pdf_bytes.startswith(b"%PDF-"):
            # 실제 PDF 파일인지 간단히 체크. PDF 파일이 아니면 예외를 발생시킵니다.
            logger.warning("[%s] PDF 시그니처 검증 실패", request_id)
            raise HTTPException(status_code=400, detail="manual_file_path는 PDF 파일이어야 합니다.")

        # PDF와 상품명을 LLM API에 보내서 판매대본 생성
        logger.info("[%s] LLM 호출 시작", request_id)
        answer_text = _call_llm_generate(
            manual_pdf_bytes=manual_pdf_bytes,
            manual_file_name=body.manual_file_name,
        )
        logger.info("[%s] LLM 호출 완료", request_id)

        try:
            generated = _parse_json_from_text(answer_text)
            logger.info("[%s] LLM 응답 JSON 파싱 성공", request_id)
        except Exception:
            logger.warning("[%s] LLM 응답 JSON 파싱 실패, 원문 저장", request_id)
            generated = {
                "raw_text": answer_text,
                "warning": "LLM 응답을 JSON으로 파싱하지 못해 원문을 저장했습니다.",
            }

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        script_name = body.manual_file_name.strip() if body.manual_file_name and body.manual_file_name.strip() else manual_path.stem
        out_name = f"{ts}_{_safe_tag(script_name)}_{_safe_tag(SYSTEM_PROMPT_VERSION)}.json"
        output_path = OUTPUT_DIR / out_name
        output_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info("[%s] generate 결과 저장 완료 | path=%s | elapsed=%dms", request_id, output_path, elapsed_ms)

        return {
            "saved_path": str(output_path),
            "prompt_version": SYSTEM_PROMPT_VERSION,
            "result": generated,
        }
    except HTTPException as e:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning("[%s] 요청 처리 실패(HTTP) | status=%s | detail=%s | elapsed=%dms", request_id, e.status_code, e.detail, elapsed_ms)
        raise
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.exception("[%s] 요청 처리 실패(Exception) | elapsed=%dms", request_id, elapsed_ms)
        raise HTTPException(status_code=500, detail=f"서버 오류: {type(e).__name__}: {e}")
