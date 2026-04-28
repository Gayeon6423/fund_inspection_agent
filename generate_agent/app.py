import csv
import json
import os
import threading
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

import streamlit as st

try:
    from generate_agent import api_server as api
except ImportError:
    import api_server as api


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads_local"
EXTERNAL_UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads_external"
APP_LOG_DIR = PROJECT_ROOT / "data" / "log"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output_generate_agent"


def get_setting(name: str, default: str | None = None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


def to_csv_bytes(data: dict) -> bytes:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["key", "value"])
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False)
        else:
            value_text = str(value)
        writer.writerow([key, value_text])
    return output.getvalue().encode("utf-8-sig")


def to_table_rows(data: dict) -> list[dict]:
    rows = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            value_text = str(value)
        rows.append({"항목": key, "내용": value_text})
    return rows


def render_generate_page():
    st.markdown("### 판매대본 생성")
    if not api.API_KEY:
        st.error("API_KEY가 설정되지 않았습니다. Streamlit Cloud Secrets에 API_KEY를 추가해주세요.")
        return

    uploaded_pdf = st.file_uploader("상품설명서 PDF 업로드", type=["pdf"], key="generate_pdf")
    if uploaded_pdf is not None:
        st.markdown(
            f"""
            <div style="font-size:13px; margin-top:4px; margin-bottom:8px;">
              <span style="opacity:0.75;">업로드 파일명:</span>
              <div style="word-break:break-all; margin-top:2px;">{uploaded_pdf.name}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if st.button("판매대본 생성", type="primary", disabled=uploaded_pdf is None, key="generate_run"):
        if uploaded_pdf is None:
            st.error("PDF 파일을 먼저 업로드해주세요.")
            return

        pdf_bytes = uploaded_pdf.getvalue()
        if not pdf_bytes.startswith(b"%PDF-"):
            st.error("PDF 파일이 아닙니다. PDF를 업로드해주세요.")
            return

        status_box = st.empty()
        started_at = time.time()
        result_holder = {"answer_text": None, "error": None}

        def _worker():
            try:
                result_holder["answer_text"] = api._call_llm_generate(
                    manual_pdf_bytes=pdf_bytes,
                    manual_file_name=uploaded_pdf.name,
                )
            except Exception as e:
                result_holder["error"] = e

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        while thread.is_alive():
            elapsed = int(time.time() - started_at)
            status_box.info(f"🔄 판매대본 생성 중... ({elapsed}초 경과)")
            time.sleep(1)

        if result_holder["error"] is not None:
            err = result_holder["error"]
            detail = getattr(err, "detail", str(err))
            status_box.info("❌ 판매대본 생성 실패")
            st.error(f"생성 중 오류가 발생했습니다: {detail}")
            return

        answer_text = result_holder["answer_text"]
        try:
            generated = api._parse_json_from_text(answer_text)
        except Exception:
            generated = {
                "raw_text": answer_text,
                "warning": "LLM 응답을 JSON으로 파싱하지 못해 원문을 저장했습니다.",
            }

        total_elapsed = int(time.time() - started_at)
        status_box.info(f"✅ 판매대본 분석 완료! (총 {total_elapsed}초 소요)")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name_raw = uploaded_pdf.name.rsplit(".", 1)[0]
        base_name = api._safe_tag(base_name_raw)
        prompt_version = api._safe_tag(api.SYSTEM_PROMPT_VERSION)

        json_name = f"생성결과_{ts}_{base_name}.json"
        csv_name = f"생성결과_{ts}_{base_name}.csv"

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = OUTPUT_DIR / json_name
        json_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8")

        json_bytes = json.dumps(generated, ensure_ascii=False, indent=2).encode("utf-8")
        csv_bytes = to_csv_bytes(generated)
        rows = to_table_rows(generated)

        st.info("판매대본 파일 생성 및 저장이 완료되었습니다.")

        st.markdown("#### 생성 결과 테이블")
        st.dataframe(rows, use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "JSON 다운로드",
                data=json_bytes,
                file_name=json_name,
                mime="application/json",
                key="generate_download_json",
            )
        with col2:
            st.download_button(
                "CSV 다운로드",
                data=csv_bytes,
                file_name=csv_name,
                mime="text/csv",
                key="generate_download_csv",
            )

def render_log_page():
    st.markdown("### 로그")
    log_type = st.radio(
        "로그 종류",
        options=["서버 실행 로그(.log)", "분석결과(JSON)"],
        horizontal=True,
        key="log_type",
    )

    if log_type == "서버 실행 로그(.log)":
        APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        generate_logs = sorted(APP_LOG_DIR.glob("generate_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        logs = generate_logs or sorted(APP_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            st.info("data/log 폴더에 .log 파일이 없습니다.")
            return

        selected = st.selectbox("로그 파일 선택", options=[p.name for p in logs], key="runtime_log_select")
        selected_path = APP_LOG_DIR / selected
        st.text_area(
            "로그 내용",
            value=selected_path.read_text(encoding="utf-8", errors="replace"),
            height=520,
            disabled=True,
        )
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not results:
        st.info("data/output_generate_agent 폴더에 분석결과 JSON이 없습니다.")
        return

    selected = st.selectbox("분석결과 파일 선택", options=[p.name for p in results], key="result_json_select")
    selected_path = OUTPUT_DIR / selected

    try:
        payload = json.loads(selected_path.read_text(encoding="utf-8"))
        st.markdown("#### 결과 테이블")
        st.dataframe(to_table_rows(payload), use_container_width=True, hide_index=True)
        with st.expander("원본 JSON 보기"):
            st.json(payload)
    except Exception as e:
        st.error(f"결과 파일을 읽지 못했습니다: {e}")


def main():
    st.set_page_config(page_title="펀드판매대본 생성 시스템", layout="wide")
    api.API_KEY = get_setting("API_KEY")
    api.MODEL = get_setting("LLM_MODEL", api.MODEL)
    api.SYSTEM_PROMPT_VERSION = get_setting("GENERATE_SYSTEM_PROMPT_VERSION", api.SYSTEM_PROMPT_VERSION)

    st.markdown(
    """
    <style>
    .app-header { background:var(--secondary-background-color); border:1px solid rgba(128,128,128,0.25);
                    border-radius:14px; padding:16px 18px; margin-bottom:14px; }
    .app-header .title    { font-size:30px; font-weight:800; color:var(--text-color); line-height:1.2; }
    .app-header .subtitle { font-size:16px; font-weight:600; color:var(--text-color); opacity:0.8; margin-top:6px; }
    </style>
    <div class="app-header">
        <div class="title">📝 펀드판매대본 생성 시스템</div>
        <div class="subtitle">펀드 상품 설명서를 입력하면 판매 대본을 생성합니다.</div>
    </div>
    """,
    unsafe_allow_html=True,
    )

    st.sidebar.markdown("### 메뉴")
    menu = st.sidebar.radio("페이지", options=["판매대본 생성", "로그"], key="main_menu")

    if menu == "판매대본 생성":
        render_generate_page()
    else:
        render_log_page()


if __name__ == "__main__":
    main()
