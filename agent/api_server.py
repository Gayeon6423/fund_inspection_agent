import base64
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=True)

# ── 환경변수 ──────────────────────────────────────────────
api_key = os.getenv("ANTHROPIC_API_KEY")
system_prompt = os.getenv("SYSTEM_PROMPT_VERSION")
if not api_key:
    print("에러: ANTHROPIC_API_KEY 환경변수를 먼저 설정해주세요.")
    sys.exit(1)

MODEL        = os.getenv("LLM_MODEL")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR   = PROJECT_ROOT / "data" / "output_agent"

_prompt_path = Path(__file__).resolve().parent / "prompt" / f"{system_prompt}.txt"
with open(_prompt_path, encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()

# ── FastAPI 앱 ────────────────────────────────────────────
app = FastAPI(
    title="Fund Agent API",
    version="1.0.0",
    description="Claude 기반 펀드 AI 에이전트 API",
)

# ── 요청 스키마 ───────────────────────────────────────────
class AskRequest(BaseModel):
    input_type: str = "text"              # "text" | "json_file" | "pdf_file" | "compare"
    user_query: Optional[str] = None      # input_type="text" 일 때
    file_path: Optional[str] = None       # input_type="json_file" | "pdf_file" 일 때
    script_file_path: Optional[str] = None  # input_type="compare": 판매대본 JSON
    manual_file_path: Optional[str] = None  # input_type="compare": 제안서 PDF


# ── 파일 경로 해석 (data 폴더 재귀 탐색 포함) ────────────
def resolve_path(relative: str) -> Path:
    """프로젝트 루트 기준 상대경로 → 절대경로. 없으면 data/ 폴더에서 재귀 탐색."""
    path = PROJECT_ROOT / relative
    if path.exists():
        return path
    # 파일명만으로 data/ 아래 재귀 탐색
    matches = list((PROJECT_ROOT / "data").rglob(Path(relative).name))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail=f"동일 파일명이 여러 개입니다: {[str(m) for m in matches]}")
    raise HTTPException(status_code=400, detail=f"파일을 찾을 수 없습니다: {relative}")


# ── 입력 컨텐츠 빌더 ─────────────────────────────────────
def build_user_content(body: AskRequest) -> list:
    if body.input_type == "text":
        if not body.user_query:
            raise HTTPException(status_code=400, detail="text 모드에서는 user_query가 필요합니다.")
        return [{"type": "text", "text": body.user_query}]

    if body.input_type == "json_file":
        if not body.file_path:
            raise HTTPException(status_code=400, detail="json_file 모드에서는 file_path가 필요합니다.")
        path = resolve_path(body.file_path)
        print(f"[JSON 로드] {path}")
        with path.open(encoding="utf-8") as f:
            content = json.load(f)
        return [{"type": "text", "text": json.dumps(content, ensure_ascii=False, indent=2)}]

    if body.input_type == "pdf_file":
        if not body.file_path:
            raise HTTPException(status_code=400, detail="pdf_file 모드에서는 file_path가 필요합니다.")
        path = resolve_path(body.file_path)
        print(f"[PDF 로드] {path}")
        with path.open("rb") as f:
            pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}}]

    if body.input_type == "compare":
        if not body.script_file_path or not body.manual_file_path:
            raise HTTPException(status_code=400, detail="compare 모드에서는 script_file_path와 manual_file_path가 필요합니다.")

        script_path = resolve_path(body.script_file_path)
        print(f"[판매대본 로드] {script_path}")
        with script_path.open(encoding="utf-8") as f:
            script_content = json.load(f)

        manual_path = resolve_path(body.manual_file_path)
        print(f"[제안서 로드] {manual_path}")
        with manual_path.open("rb") as f:
            pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

        return [
            {"type": "text", "text": f"[판매대본 JSON]\n{json.dumps(script_content, ensure_ascii=False, indent=2)}"},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
        ]

    raise HTTPException(status_code=400, detail=f"지원하지 않는 input_type: {body.input_type}")


# ── Claude API 호출 ───────────────────────────────────────
def call_claude(user_content: list) -> str:
    payload = {
        "model": MODEL,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if any(block.get("type") == "document" for block in user_content):
        headers["anthropic-beta"] = "pdfs-2024-09-25"

    print(f"[Claude 호출] model={MODEL}, blocks={[b['type'] for b in user_content]}")
    req = urllib.request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"Claude API 오류: {body}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"네트워크 오류: {e.reason}")


# ── 엔드포인트: 질문 → 답변 ───────────────────────────────
@app.post("/v1/agent/fund/ask")
def ask(body: AskRequest):
    try:
        user_content = build_user_content(body)
        print("[Claude 요청 시작]")
        answer = call_claude(user_content)

        start = answer.find("{")
        end = answer.rfind("}") + 1
        if start == -1 or end == 0:
            raise HTTPException(status_code=500, detail=f"Claude 응답에서 JSON을 찾을 수 없습니다: {answer[:300]}")
        try:
            parsed = json.loads(answer[start:end])
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=500, detail=f"JSON 파싱 실패: {e}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{system_prompt}_{timestamp}.json"
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        print(f"[저장 완료] {output_path}")

        return parsed

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"서버 오류: {type(e).__name__}: {e}")


# ── 헬스체크 ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}
