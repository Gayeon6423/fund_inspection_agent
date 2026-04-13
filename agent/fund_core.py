"""
fund_core.py — 공유 비즈니스 로직
app.py(Streamlit)와 api_server.py(FastAPI) 양쪽에서 import해서 사용합니다.
"""
import base64
import json
import re
import urllib.error
import urllib.request
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=True)
API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("LLM_MODEL")
SYSTEM_PROMPT_VERSION = os.getenv("SYSTEM_PROMPT_VERSION", "system_prompt_v10")
_prompt_path = Path(__file__).resolve().parent / "prompt" / f"{SYSTEM_PROMPT_VERSION}.txt"
with open(_prompt_path, encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()

META_KEYS = {"category", "summary", "summary_script", "summary_manual", "mismatches"}


# ── LLM API ────────────────────────────────────────────

def call_llm(user_content: list, model: str, api_key: str, system_prompt: str) -> str:
    """범용 LLM API 호출. user_content는 메시지 content 블록 리스트."""
    payload = {
        "model": model,
        "max_tokens": 8000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if any(block.get("type") == "document" for block in user_content):
        headers["anthropic-beta"] = "pdfs-2024-09-25"

    req = urllib.request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["content"][0]["text"]


def call_llm_compare(script_json: dict, manual_pdf_bytes: bytes,
                        model: str, api_key: str, system_prompt: str) -> str:
    """판매대본 JSON + 설명서 PDF를 LLM에 전달해 비교 분석 결과(텍스트)를 반환."""
    if not isinstance(manual_pdf_bytes, (bytes, bytearray)):
        raise TypeError("manual_pdf_bytes는 bytes 타입이어야 합니다.")
    if not manual_pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("manual_pdf_bytes는 실제 PDF 파일 바이트여야 합니다.")

    user_content = [
        {
            "type": "text",
            "text": f"[판매대본 JSON]\n{json.dumps(script_json, ensure_ascii=False, indent=2)}",
        },
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(manual_pdf_bytes).decode("utf-8"),
            },
        },
    ]
    return call_llm(user_content, model, api_key, system_prompt)


# ── 응답 파싱 ─────────────────────────────────────────────

# ── 결과 처리 ─────────────────────────────────────────────
def _to_cell_text(value) -> str:
    """LLM 반환 필드를 표 셀 표시용 문자열로 정규화."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(x) for x in value if x is not None).strip()
    if isinstance(value, dict):
        return "\n".join(f"{k}: {v}" for k, v in value.items()).strip()
    return str(value).strip()


def _pick_manual_fallback(item_key: str, summary_manual) -> str:
    """summary_manual에서 항목명과 가까운 근거를 찾아 문자열로 반환."""
    if isinstance(summary_manual, dict):
        key_text = str(item_key or "")
        tokens = [t for t in re.split(r"[\s,\-_/()]+", key_text) if len(t) >= 2]
        hits = []
        for k, v in summary_manual.items():
            k_text = str(k)
            if any(token in k_text or k_text in token for token in tokens):
                hits.append(f"{k_text}: {_to_cell_text(v)}")
        if hits:
            return "\n".join(hits[:2])
        # 항목 매칭 실패 시 억지 값을 채우지 않는다.
        return "-"
    return "-"


def build_comparison_rows(result_json: dict, script_json: Optional[dict] = None, summary_manual=None) -> list:
    """결과 JSON → 비교 표 행 리스트 [{"항목", "판정", "판매대본", "설명서", "근거"}, ...]"""
    rows = []
    for key, value in result_json.items():
        if key in META_KEYS:
            continue
        if isinstance(value, list) and value:
            label = value[0]
            if label not in ("일치", "불일치"):
                continue

            script_text = _to_cell_text(value[1]) if len(value) > 1 else ""
            manual_text = _to_cell_text(value[2]) if len(value) > 2 else ""
            reason = _to_cell_text(value[3]) if len(value) > 3 else ""

            # 구버전 포맷 호환: ["불일치", "근거: ...", "불일치 근거: ..."]
            if len(value) == 3 and label == "불일치":
                if not manual_text:
                    manual_text = _to_cell_text(value[1])
                if not reason:
                    reason = _to_cell_text(value[2])

            # 값이 비어 있으면 UI에서 빈칸 대신 기본값 표시
            if not script_text and isinstance(script_json, dict):
                script_text = _to_cell_text(script_json.get(key, ""))
            script_text = script_text or "-"

            if not manual_text:
                manual_text = _pick_manual_fallback(key, summary_manual)
            manual_text = manual_text or "-"

            if label == "일치":
                reason = reason or "판매대본과 설명서의 핵심 내용이 일치합니다."
            else:
                reason = reason or "-"

            rows.append({
                "항목": key,
                "판정": label,
                "판매대본": script_text,
                "설명서": manual_text,
                "근거": reason,
            })
            

    if not rows and isinstance(result_json.get("mismatches"), list):
        for item in result_json["mismatches"]:
            rows.append({"항목": "mismatches", "판정": "불일치", "판매대본": "-", "설명서": "-", "근거": str(item)})
    return rows


def summary_manual_to_text(summary_manual) -> str:
    """summary_manual 필드를 사람이 읽을 수 있는 문자열로 변환."""
    if isinstance(summary_manual, dict):
        lines = [f"- {k}: {v}" for k, v in summary_manual.items()]
        return "\n".join(lines) if lines else "-"
    return summary_manual if summary_manual else "-"



def _extract_json_block(answer_text: str) -> str:
    """Extract the first complete JSON object from free-form text."""
    candidates = []
    fence_patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
    ]
    for pattern in fence_patterns:
        candidates.extend(re.findall(pattern, answer_text, flags=re.DOTALL))
    candidates.append(answer_text)

    for text in candidates:
        start = text.find("{")
        if start == -1:
            continue

        depth = 0
        in_string = False
        escaped = False

        for i in range(start, len(text)):
            ch = text[i]
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
                    return text[start:i + 1]

    raise ValueError("LLM 응답에서 완전한 JSON 객체를 찾지 못했습니다.")


def parse_json_from_text(answer_text: str) -> dict:
    """LLM 응답 텍스트에서 JSON 본문을 추출해 파싱."""
    json_text = _extract_json_block(answer_text)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        lines = json_text.splitlines()
        bad_line = lines[e.lineno - 1] if 1 <= e.lineno <= len(lines) else ""
        raise ValueError(
            f"LLM JSON 파싱 실패: {e.msg} (line {e.lineno}, column {e.colno}). "
            f"문제 줄: {bad_line[:200]}"
        ) from e
if __name__ == "__main__":
    # 간단한 테스트
    user_content = [{"type": "text", "text": "삼성전자의 종목코드는?"}]
    response = call_llm(user_content, MODEL, API_KEY, SYSTEM_PROMPT)
    print("LLM 응답:", response)

