import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent
PROMPT_DIR = MODULE_DIR / "prompt"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output_generate_agent"

load_dotenv(find_dotenv(), override=True)

API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
SYSTEM_PROMPT_VERSION = os.getenv("GENERATE_SYSTEM_PROMPT_VERSION", "system_prompt_v1")


class GenerateRequest(BaseModel):
    manual_file_path: str
    instruction: Optional[str] = None
    product_name: Optional[str] = None


def _safe_tag(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def _resolve_prompt_path() -> Path:
    prompt_path = PROMPT_DIR / f"{SYSTEM_PROMPT_VERSION}.txt"
    if prompt_path.exists():
        return prompt_path

    fallback = PROMPT_DIR / "system_prompt_v1.txt"
    if fallback.exists():
        return fallback

    raise FileNotFoundError(
        f"프롬프트 파일을 찾을 수 없습니다: {prompt_path} (fallback: {fallback})"
    )


def _load_system_prompt() -> str:
    return _resolve_prompt_path().read_text(encoding="utf-8").strip()


def _resolve_path(user_path: str) -> Path:
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
    return json.loads(_extract_json_block(answer_text))


def _call_llm_generate(manual_pdf_bytes: bytes, instruction: Optional[str], product_name: Optional[str]) -> str:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY 환경변수가 설정되어 있지 않습니다.")

    user_text = [
        "다음 상품설명서 PDF를 기반으로 판매대본을 생성해줘.",
        "출력은 반드시 JSON으로 작성해줘.",
    ]
    if product_name:
        user_text.append(f"상품명: {product_name}")
    if instruction:
        user_text.append(f"추가 지시사항: {instruction}")

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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL,
        "prompt_version": SYSTEM_PROMPT_VERSION,
        "prompt_exists": _resolve_prompt_path().exists() if PROMPT_DIR.exists() else False,
    }


@app.post("/v1/agent/fund/generate-script")
def generate_script(body: GenerateRequest):
    manual_path = _resolve_path(body.manual_file_path)
    manual_pdf_bytes = manual_path.read_bytes()

    if not manual_pdf_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="manual_file_path는 PDF 파일이어야 합니다.")

    answer_text = _call_llm_generate(
        manual_pdf_bytes=manual_pdf_bytes,
        instruction=body.instruction,
        product_name=body.product_name,
    )

    try:
        generated = _parse_json_from_text(answer_text)
    except Exception:
        generated = {
            "raw_text": answer_text,
            "warning": "LLM 응답을 JSON으로 파싱하지 못해 원문을 저장했습니다.",
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"generate_{ts}_{_safe_tag(SYSTEM_PROMPT_VERSION)}.json"
    output_path = OUTPUT_DIR / out_name
    output_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "saved_path": str(output_path),
        "prompt_version": SYSTEM_PROMPT_VERSION,
        "result": generated,
    }
