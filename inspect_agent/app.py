import csv
import base64
import html
import json
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile

import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx
except ImportError:
    try:
        from streamlit.scriptrunner import add_script_run_ctx
    except ImportError:
        def add_script_run_ctx(t):
            return t

from dotenv import find_dotenv, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from inspect_agent.excel_json.excel_to_json import convert_excel_to_json_by_sheets
from fund_core import (
    call_llm_compare,
    parse_json_from_text,
    calc_match_rate,
    build_comparison_rows,
    summary_manual_to_text,
)

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"

UPLOAD_DIR            = PROJECT_ROOT / "data" / "uploads_external"
LOCAL_UPLOAD_DIR      = PROJECT_ROOT / "data" / "uploads_local"
OUTPUT_EXCEL_JSON_DIR = PROJECT_ROOT / "data" / "output_excel_json"
OUTPUT_INSPECT_AGENT_DIR      = PROJECT_ROOT / "data" / "output_inspect_agent"
APP_LOG_DIR           = PROJECT_ROOT / "data" / "log"
PROMPT_DIR            = Path(__file__).resolve().parent / "prompt"


# ── 유틸 ─────────────────────────────────────────────────

def safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value).strip()


def start_inspect_log_run() -> Path:
    APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = APP_LOG_DIR / f"inspect_{run_ts}.log"
    st.session_state["inspect_log_path"] = str(log_path)
    return log_path


def append_inspect_log(message: str, log_path: Path | None = None):
    APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    target = log_path
    if target is None:
        saved = st.session_state.get("inspect_log_path")
        target = Path(saved) if saved else None
    if target is None:
        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = APP_LOG_DIR / f"inspect_{run_ts}.log"
        st.session_state["inspect_log_path"] = str(target)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} | INFO | {message}\n"
    try:
        with target.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # 로그 기록 실패가 본 분석 플로우를 깨지 않도록 보호
        pass


def parse_sheet_names_from_xlsx_bytes(raw: bytes):
    with ZipFile(BytesIO(raw)) as zf:
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for rel in rel_root.findall(f"{{{NS_REL_PACKAGE}}}Relationship"):
            rel_map[rel.attrib["Id"]] = rel.attrib["Target"]
        names = []
        for sheet in wb_root.findall(f".//{{{NS_MAIN}}}sheet"):
            rid = sheet.attrib.get(f"{{{NS_REL_OFFICE}}}id")
            if rid in rel_map:
                names.append(sheet.attrib.get("name", ""))
        return names


def get_setting(name: str, default: str | None = None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        # secrets.toml이 없어도 .env/os.environ fallback 동작을 유지
        pass
    return os.getenv(name, default)


def rows_to_csv(rows: list) -> bytes:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["항목", "판정", "판매대본", "설명서", "근거"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def to_preview_text(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def render_full_filename_in_sidebar(label: str, filename: str):
    st.sidebar.markdown(
        f"""
        <div style="font-size:13px; margin-top:4px; margin-bottom:8px;">
          <span style="opacity:0.75;">{html.escape(label)}:</span>
          <div style="word-break:break-all; margin-top:2px;">{html.escape(filename)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_pdf_preview(pdf_bytes: bytes, height: int = 760):
    """PDF 미리보기. st.pdf 지원 시 우선 사용하고, 없으면 PDF.js로 렌더."""
    if hasattr(st, "pdf"):
        try:
            st.pdf(pdf_bytes)
            return
        except Exception:
            pass

    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    viewer_html = f"""
    <div id="pdf-container" style="width:100%; height:{height}px; overflow:auto; border:1px solid rgba(128,128,128,0.25); border-radius:8px; background:white;"></div>
    <script type="module">
      import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs";
      pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs";

      const raw = atob("{pdf_b64}");
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) {{
        bytes[i] = raw.charCodeAt(i);
      }}

      const container = document.getElementById("pdf-container");
      const loadingTask = pdfjsLib.getDocument({{ data: bytes }});
      const pdf = await loadingTask.promise;
      for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {{
        const page = await pdf.getPage(pageNum);
        const viewport = page.getViewport({{ scale: 1.25 }});
        const canvas = document.createElement("canvas");
        canvas.style.display = "block";
        canvas.style.margin = "10px auto";
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const context = canvas.getContext("2d");
        await page.render({{ canvasContext: context, viewport }}).promise;
        container.appendChild(canvas);
      }}
    </script>
    """
    components.html(viewer_html, height=height + 20, scrolling=True)


# ── UI 컴포넌트 ───────────────────────────────────────────

def to_html_text(value):
    if value is None:
        return "-"
    return html.escape(str(value)).replace("\n", "<br>")


def render_info_block(title: str, body):
    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.2); border-radius:14px; padding:14px;
                    background:var(--secondary-background-color); height:100%;">
          <div style="font-size:14px; color:var(--text-color); opacity:0.65; margin-bottom:8px;">{to_html_text(title)}</div>
          <div style="font-size:15px; line-height:1.6; color:var(--text-color);">{to_html_text(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_icon(status: str) -> str:
    if "오류" in status:
        return "❌"
    if "중" in status:
        return "🔄"
    if "완료" in status:
        return "✅"
    return "⏳"


def render_status_panel(container, sheets, convert_status_map, analyze_status_map):
    with container.container():
        if not sheets:
            # st.caption("시트를 선택하면 상태가 표시됩니다.")
            return
        st.markdown("### 변환 상태")
        for sheet in sheets:
            status = convert_status_map.get(sheet, "대기")
            st.markdown(f"- {status_icon(status)} **{sheet}**: {status}")
        st.markdown("### 분석 상태")
        for sheet in sheets:
            status = analyze_status_map.get(sheet, "대기")
            st.markdown(f"- {status_icon(status)} **{sheet}**: {status}")


def render_comparison_table(rows, verdict_filter: str):
    if verdict_filter != "전체":
        rows = [r for r in rows if r["판정"] == verdict_filter]
    if not rows:
        st.info("조건에 맞는 항목이 없습니다.")
        return

    st.markdown(
        """
        <style>
        .cmp-table { width:100%; border-collapse:collapse; font-size:14px; color:var(--text-color); }
        .cmp-table th {
            text-align:left; background:var(--secondary-background-color);
            color:var(--text-color); padding:10px; border:1px solid rgba(128,128,128,0.25);
        }
        .cmp-table td { vertical-align:top; padding:10px; border:1px solid rgba(128,128,128,0.25); color:var(--text-color); }
        .badge-ok { color:#065f46; font-weight:700; background:#d1fae5; padding:3px 8px; border-radius:999px; }
        .badge-no { color:#991b1b; font-weight:700; background:#fee2e2; padding:3px 8px; border-radius:999px; }
        .memo { display:block; white-space:pre-wrap; line-height:1.5; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    table_html = [
        "<table class='cmp-table'>",
        "<thead><tr><th style='width:22%'>항목</th><th style='width:8%'>판정</th><th style='width:25%'>판매대본</th><th style='width:25%'>설명서</th><th>근거</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        verdict = row["판정"]
        badge = "<span class='badge-ok'>일치</span>" if verdict == "일치" else "<span class='badge-no'>불일치</span>"
        script_cell = html.escape(str(row.get("판매대본", "-") or "-")).replace("\n", "<br>")
        manual_cell = html.escape(str(row.get("설명서", "-") or "-")).replace("\n", "<br>")
        reason_cell = html.escape(str(row.get("근거", "-") or "-")).replace("\n", "<br>")
        table_html.append(
            "<tr>"
            f"<td>{html.escape(str(row['항목']))}</td>"
            f"<td>{badge}</td>"
            f"<td><span class='memo'>{script_cell}</span></td>"
            f"<td><span class='memo'>{manual_cell}</span></td>"
            f"<td><span class='memo'>{reason_cell}</span></td>"
            "</tr>"
        )
    table_html.append("</tbody></table>")
    st.markdown("".join(table_html), unsafe_allow_html=True)


def render_data_page():
    st.markdown("### 데이터 조회")
    LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local_files = sorted([p for p in LOCAL_UPLOAD_DIR.iterdir() if p.is_file()], key=lambda p: p.name)
    if not local_files:
        st.info("data/uploads_local 폴더에 파일이 없습니다.")
        return

    selected_data_name = st.selectbox(
        "데이터 파일 선택",
        options=[p.name for p in local_files],
        key="data_logs_data_file",
    )
    selected_data_path = LOCAL_UPLOAD_DIR / selected_data_name
    # st.caption(f"경로: {selected_data_path}")
    suffix = selected_data_path.suffix.lower()

    if suffix == ".xlsx":
        try:
            xlsx_bytes = selected_data_path.read_bytes()
            st.download_button(
                "엑셀 다운로드",
                data=xlsx_bytes,
                file_name=selected_data_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="data_logs_xlsx_download",
            )
        except Exception as e:
            st.error(f"엑셀 파일 읽기에 실패했습니다: {e}")
            xlsx_bytes = b""

        try:
            sheet_names = parse_sheet_names_from_xlsx_bytes(xlsx_bytes if xlsx_bytes else selected_data_path.read_bytes())
        except Exception as e:
            st.error(f"시트 목록을 읽지 못했습니다: {e}")
            sheet_names = []
        if sheet_names:
            selected_sheet = st.selectbox(
                "시트명 선택",
                options=sheet_names,
                key="data_logs_sheet_name",
            )
            if st.button("표 확인", use_container_width=True, key="data_logs_preview_btn"):
                try:
                    OUTPUT_EXCEL_JSON_DIR.mkdir(parents=True, exist_ok=True)
                    conversion = convert_excel_to_json_by_sheets(
                        input_path=selected_data_path,
                        sheet_names=[selected_sheet],
                        output_dir=OUTPUT_EXCEL_JSON_DIR,
                    )[0]
                    payload = json.loads(Path(conversion["output_path"]).read_text(encoding="utf-8"))
                    preview_rows = [{"항목": k, "내용": to_preview_text(v)} for k, v in payload.items()]
                    st.session_state["data_logs_preview_rows"] = preview_rows
                    st.session_state["data_logs_preview_key"] = f"{selected_data_name}::{selected_sheet}"
                except Exception as e:
                    st.error(f"표 생성에 실패했습니다: {e}")

            preview_key = f"{selected_data_name}::{selected_sheet}"
            if st.session_state.get("data_logs_preview_key") == preview_key:
                preview_rows = st.session_state.get("data_logs_preview_rows", [])
                if preview_rows:
                    st.dataframe(preview_rows, use_container_width=True, hide_index=True)
                else:
                    st.info("표시할 데이터가 없습니다.")
        else:
            st.warning("선택한 엑셀 파일에서 시트를 찾지 못했습니다.")
    elif suffix == ".json":
        try:
            payload = json.loads(selected_data_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                preview_rows = [{"항목": k, "내용": to_preview_text(v)} for k, v in payload.items()]
                st.dataframe(preview_rows, use_container_width=True, hide_index=True)
            elif isinstance(payload, list):
                st.dataframe(payload, use_container_width=True, hide_index=True)
            else:
                st.text_area("JSON 내용", value=to_preview_text(payload), height=420, disabled=True)
        except Exception as e:
            st.error(f"JSON 파일 읽기에 실패했습니다: {e}")
    elif suffix == ".csv":
        csv_rows = None
        for enc in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                with selected_data_path.open("r", encoding=enc, newline="") as f:
                    csv_rows = list(csv.DictReader(f))
                break
            except Exception:
                continue
        if csv_rows is None:
            st.error("CSV 파일 인코딩을 해석하지 못했습니다.")
        elif not csv_rows:
            st.info("CSV 파일에 데이터가 없습니다.")
        else:
            st.dataframe(csv_rows, use_container_width=True, hide_index=True)
    elif suffix == ".pdf":
        try:
            pdf_bytes = selected_data_path.read_bytes()
            st.download_button(
                "PDF 다운로드",
                data=pdf_bytes,
                file_name=selected_data_path.name,
                mime="application/pdf",
                use_container_width=True,
                key="data_logs_pdf_download",
            )
            render_pdf_preview(pdf_bytes, height=760)
        except Exception as e:
            st.error(f"PDF 파일 미리보기에 실패했습니다: {e}")
    else:
        st.info("표 미리보기는 xlsx/json/csv 형식만 지원합니다.")


def render_log_page():
    st.markdown("### 로그 조회")
    log_type = st.radio(
        "로그 종류",
        options=["서버 실행 로그", "분석 결과"],
        horizontal=True,
        key="data_logs_type",
    )

    if log_type == "서버 실행 로그":
        APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        inspect_logs = sorted(
            [p for p in APP_LOG_DIR.glob("inspect_*.log") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        runtime_logs = inspect_logs or sorted(
            [p for p in APP_LOG_DIR.glob("*.log") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not runtime_logs:
            st.info("data/log 폴더에 .log 파일이 없습니다.")
            return

        selected_log_name = st.selectbox(
            "실행 로그 파일 선택",
            options=[p.name for p in runtime_logs],
            key="data_logs_runtime_file",
        )
        selected_log_path = APP_LOG_DIR / selected_log_name
        try:
            st.text_area(
                "로그 내용",
                value=selected_log_path.read_text(encoding="utf-8", errors="replace"),
                height=520,
                disabled=True,
            )
        except Exception as e:
            st.error(f"실행 로그 파일을 읽지 못했습니다: {e}")
        return

    OUTPUT_INSPECT_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    result_logs = sorted(
        [p for p in OUTPUT_INSPECT_AGENT_DIR.iterdir() if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not result_logs:
        st.info("data/output_inspect_agent 폴더에 분석 결과 파일이 없습니다.")
        return

    selected_log_name = st.selectbox(
        "분석 결과 파일 선택",
        options=[p.name for p in result_logs],
        key="data_logs_result_file",
    )
    selected_log_path = OUTPUT_INSPECT_AGENT_DIR / selected_log_name
    try:
        if selected_log_path.suffix.lower() == ".json":
            st.json(json.loads(selected_log_path.read_text(encoding="utf-8")))
        else:
            st.text_area(
                "결과 내용",
                value=selected_log_path.read_text(encoding="utf-8", errors="replace"),
                height=520,
                disabled=True,
            )
    except Exception as e:
        st.error(f"분석 결과 파일을 읽지 못했습니다: {e}")


# ── 메인 ─────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="펀드판매대본 점검", layout="wide")
    load_dotenv(find_dotenv(), override=True)

    api_key            = get_setting("API_KEY")
    model              = get_setting("LLM_MODEL")
    INSPECT_SYSTEM_PROMPT_VERSION = (
        get_setting("INSPECT_SYSTEM_PROMPT_VERSION")
        or get_setting("SYSTEM_PROMPT_VERSION")
        or "inspect_system_prompt_v11"
    )

    prompt_path = PROMPT_DIR / f"{INSPECT_SYSTEM_PROMPT_VERSION}.txt"
    if not prompt_path.exists():
        fallback_candidates = [
            PROMPT_DIR / "inspect_system_prompt_v11.txt",
            PROMPT_DIR / "system_prompt_v11.txt",
            PROMPT_DIR / "system_prompt_v5.txt",
        ]
        for candidate in fallback_candidates:
            if candidate.exists():
                prompt_path = candidate
                INSPECT_SYSTEM_PROMPT_VERSION = candidate.stem
                break
        else:
            st.error(
                f"프롬프트 파일이 없습니다: {prompt_path}\n"
                "INSPECT_SYSTEM_PROMPT_VERSION 또는 SYSTEM_PROMPT_VERSION 값을 확인해주세요."
            )
            st.stop()
    default_system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    active_system_prompt = default_system_prompt
    active_prompt_tag = INSPECT_SYSTEM_PROMPT_VERSION

    st.markdown(
        """
        <style>
        .app-header { background:var(--secondary-background-color); border:1px solid rgba(128,128,128,0.25);
                      border-radius:14px; padding:16px 18px; margin-bottom:14px; }
        .app-header .title    { font-size:30px; font-weight:800; color:var(--text-color); line-height:1.2; }
        .app-header .subtitle { font-size:16px; font-weight:600; color:var(--text-color); opacity:0.8; margin-top:6px; }
        </style>
        <div class="app-header">
          <div class="title">🚨 펀드판매대본 점검 시스템</div>
          <div class="subtitle">펀드 판매대본과 상품 설명서의 일치도를 분석합니다.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        """
        <style>
        section[data-testid="stSidebar"] div[data-testid="stRadio"] {
            background: rgba(107,114,128,0.14);
            border: 1px solid rgba(128,128,128,0.35);
            border-radius: 14px;
            padding: 10px 12px 8px 12px;
            margin-bottom: 14px;
        }
        section[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] label {
            border: none;
            border-radius: 0;
            padding: 4px 2px;
            margin-bottom: 2px;
            background: transparent;
        }
        section[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] label p {
            font-size: 15px !important;
            font-weight: 600 !important;
        }
        .sidebar-menu-title {
            font-size: 32px;
            font-weight: 800;
            margin: 0 0 8px 2px;
            line-height: 1.1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("<div class='sidebar-menu-title'>메뉴</div>", unsafe_allow_html=True)
    page_mode = st.sidebar.radio(
        "메뉴",
        options=["일치도 분석", "프롬프트 수정", "데이터", "로그"],
        index=0,
        label_visibility="collapsed",
    )
    if page_mode == "데이터":
        render_data_page()
        return
    elif page_mode == "로그":
        render_log_page()
        return
    elif page_mode == "프롬프트 수정":
        # st.subheader("프롬프트 수정")
        st.info("수정한 프롬프트를 기반으로 일치도 분석을 다시 시도해보세요!")

        PROMPT_DIR.mkdir(parents=True, exist_ok=True)
        prompt_files = sorted(
            [p for p in PROMPT_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".txt", ".md"}],
            key=lambda p: p.name,
        )

        left_col, right_col = st.columns(2)

        with left_col:
            st.markdown("### 프롬프트 조회")
            if not prompt_files:
                st.warning("inspect_agent/prompt 폴더에 조회 가능한 프롬프트 파일이 없습니다.")
            else:
                default_name = f"{INSPECT_SYSTEM_PROMPT_VERSION}.txt"
                default_idx = 0
                for i, p in enumerate(prompt_files):
                    if p.name == default_name:
                        default_idx = i
                        break
                selected_prompt_name = st.selectbox(
                    "파일 선택",
                    options=[p.name for p in prompt_files],
                    index=default_idx,
                    key="prompt_view_select",
                )
                selected_prompt_path = PROMPT_DIR / selected_prompt_name
                try:
                    selected_prompt_text = selected_prompt_path.read_text(encoding="utf-8")
                except Exception as e:
                    st.error(f"프롬프트 파일을 읽지 못했습니다: {e}")
                    selected_prompt_text = ""
                st.text_area(
                    "선택한 프롬프트 내용",
                    value=selected_prompt_text,
                    height=520,
                    disabled=True,
                )

        with right_col:
            st.markdown("### 프롬프트 저장")
            save_filename = st.text_input(
                "파일명",
                value="",
                placeholder="예: system_prompt_v12.txt",
                key="prompt_save_filename",
            )
            save_content = st.text_area(
                "프롬프트 내용 입력",
                value="",
                height=520,
                key="prompt_save_content",
            )
            download_filename_input = Path((save_filename or "").strip()).name
            if download_filename_input:
                download_filename = safe_name(download_filename_input)
                if "." not in download_filename:
                    download_filename = f"{download_filename}.txt"
            else:
                download_filename = "prompt.txt"
            st.download_button(
                "txt 다운로드",
                data=save_content.encode("utf-8"),
                file_name=download_filename,
                mime="text/plain",
                use_container_width=True,
                disabled=not save_content.strip(),
                key="prompt_download_btn",
            )

            if st.button("저장", type="primary", use_container_width=True, key="prompt_save_btn"):
                filename_input = Path((save_filename or "").strip()).name
                if not filename_input:
                    st.error("저장할 파일명을 입력해주세요.")
                elif not save_content.strip():
                    st.error("저장할 프롬프트 내용을 입력해주세요.")
                else:
                    safe_filename = safe_name(filename_input)
                    if "." not in safe_filename:
                        safe_filename = f"{safe_filename}.txt"
                    if Path(safe_filename).suffix.lower() not in {".txt", ".md"}:
                        st.error("프롬프트 파일 확장자는 .txt 또는 .md만 가능합니다.")
                    else:
                        save_path = PROMPT_DIR / safe_filename
                        save_path.write_text(save_content, encoding="utf-8")
                        st.success(f"저장 완료: {save_path}")
                        st.rerun()
        return

    st.sidebar.markdown(
        "### 파일 업로드 (<span style='color:#ff4b4b;'>복호화</span> 파일만 가능)",
        unsafe_allow_html=True,
    )

    script_excel = st.sidebar.file_uploader("• 판매대본 파일 업로드 (.xlsx)", type=["xlsx"])
    manual_pdf = st.sidebar.file_uploader("• 설명서 파일 업로드 (.pdf)", type=["pdf"])
    if script_excel is not None:
        render_full_filename_in_sidebar("판매대본 파일명", script_excel.name)
    if manual_pdf is not None:
        render_full_filename_in_sidebar("설명서 파일명", manual_pdf.name)

    selected_sheets = []
    if script_excel is not None:
        try:
            sheet_names = parse_sheet_names_from_xlsx_bytes(script_excel.getvalue())
            selected_sheets = st.sidebar.multiselect(
                "• 판매대본 시트 선택",
                options=sheet_names,
                default=sheet_names[:1],
            )
        except Exception as e:
            st.error(f"시트 목록을 읽지 못했습니다: {e}")
            st.stop()

    uploaded_prompt_file = st.sidebar.file_uploader(
        "• 사용자 프롬프트 파일 업로드 (.txt/.md)(선택)",
        type=["txt", "md"],
        key="uploaded_prompt_file",
    )
    if uploaded_prompt_file is not None:
        try:
            uploaded_prompt_text = uploaded_prompt_file.getvalue().decode("utf-8").strip()
        except UnicodeDecodeError:
            st.error("업로드한 프롬프트 파일은 UTF-8 인코딩이어야 합니다.")
            st.stop()
        if not uploaded_prompt_text:
            st.error("업로드한 프롬프트 파일 내용이 비어 있습니다.")
            st.stop()
        active_system_prompt = uploaded_prompt_text
        active_prompt_tag = f"upload_{safe_name(Path(uploaded_prompt_file.name).stem)}"
        st.sidebar.caption(f"현재 프롬프트: 업로드 파일 ({uploaded_prompt_file.name})")
    else:
        st.sidebar.caption(f"현재 프롬프트: 기본 ({INSPECT_SYSTEM_PROMPT_VERSION}.txt)")

    convert_status_map = st.session_state.get("convert_status", {})
    analyze_status_map = st.session_state.get("analyze_status", {})
    status_placeholder = st.sidebar.empty()
    render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

    run = st.sidebar.button("일치도 분석 실행", type="primary", use_container_width=True)
    if run:
        log_path = start_inspect_log_run()
        append_inspect_log("일치도 분석 실행 시작", log_path=log_path)
        if not api_key:
            st.error("API_KEY가 설정되지 않았습니다.")
            append_inspect_log("일치도 분석 중단 | 사유=API_KEY 미설정", log_path=log_path)
            st.stop()
        if script_excel is None or manual_pdf is None:
            st.error("판매대본 파일과 설명서 파일을 모두 업로드해주세요.")
            append_inspect_log("일치도 분석 중단 | 사유=입력 파일 누락", log_path=log_path)
            st.stop()
        if not selected_sheets:
            st.error("분석할 시트를 1개 이상 선택해주세요.")
            append_inspect_log("일치도 분석 중단 | 사유=시트 미선택", log_path=log_path)
            st.stop()

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_EXCEL_JSON_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_INSPECT_AGENT_DIR.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_upload_dir = UPLOAD_DIR / ts
        run_upload_dir.mkdir(parents=True, exist_ok=True)
        excel_upload_path = run_upload_dir / safe_name(script_excel.name)
        pdf_upload_path = run_upload_dir / safe_name(manual_pdf.name)
        excel_upload_path.write_bytes(script_excel.getvalue())
        manual_pdf_bytes = manual_pdf.getvalue()
        pdf_upload_path.write_bytes(manual_pdf_bytes)

        analysis_results = []
        source_file_name = Path(script_excel.name).stem
        convert_status_map = {s: "대기" for s in selected_sheets}
        analyze_status_map = {s: "대기" for s in selected_sheets}
        st.session_state["convert_status"] = convert_status_map
        st.session_state["analyze_status"] = analyze_status_map
        render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

        for sheet in selected_sheets:
            append_inspect_log(f"시트 분석 시작 | sheet={sheet}", log_path=log_path)
            sheet_start  = time.time()
            timer_ph     = st.empty()
            current_step = ["변환 중"]
            done_event   = threading.Event()

            def _timer_tick(ph, start, step_ref, stop):
                while not stop.wait(timeout=1):
                    elapsed = int(time.time() - start)
                    ph.info(f"[{sheet}] {step_ref[0]}... ({elapsed}초 경과)")

            timer_thread = threading.Thread(
                target=_timer_tick,
                args=(timer_ph, sheet_start, current_step, done_event),
                daemon=True,
            )
            add_script_run_ctx(timer_thread)
            timer_thread.start()

            convert_status_map[sheet] = "변환 중"
            st.session_state["convert_status"] = convert_status_map
            render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

            try:
                conversion = convert_excel_to_json_by_sheets(
                    input_path=excel_upload_path,
                    sheet_names=[sheet],
                    output_dir=OUTPUT_EXCEL_JSON_DIR,
                )[0]
                script_json = json.loads(Path(conversion["output_path"]).read_text(encoding="utf-8"))

                convert_status_map[sheet] = "변환 완료"
                analyze_status_map[sheet] = "분석 중"
                current_step[0] = "분석 중"
                st.session_state["convert_status"] = convert_status_map
                st.session_state["analyze_status"] = analyze_status_map
                render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

                max_attempts = 3
                result_json = None
                last_error = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        answer_text = call_llm_compare(
                            script_json=script_json,
                            manual_pdf_bytes=manual_pdf_bytes,
                            model=model,
                            api_key=api_key,
                            system_prompt=active_system_prompt,
                        )
                        result_json = parse_json_from_text(answer_text)
                        break
                    except (ValueError, json.JSONDecodeError) as e:
                        last_error = e
                        if attempt < max_attempts:
                            timer_ph.warning(
                                f"⚠️ [{sheet}] 응답 파싱 실패로 재시도 중 ({attempt}/{max_attempts})"
                            )
                            time.sleep(1)
                        else:
                            raise RuntimeError(
                                f"LLM 응답 JSON 파싱 실패 (최대 {max_attempts}회 시도): {e}"
                            ) from e

                if result_json is None and last_error is not None:
                    raise RuntimeError(
                        f"LLM 응답 JSON 파싱 실패 (최대 {max_attempts}회 시도): {last_error}"
                    )
                match_rate  = calc_match_rate(result_json)

                output_path = OUTPUT_INSPECT_AGENT_DIR / f"web_{ts}_{safe_name(sheet)}_{active_prompt_tag}.json"
                output_path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8")
                append_inspect_log(
                    f"시트 분석 결과 저장 완료 | sheet={sheet} | output={output_path}",
                    log_path=log_path,
                )

                analysis_results.append({
                    "sheet": sheet,
                    "source_file_name": source_file_name,
                    "script_json": script_json,
                    "match_rate": match_rate,
                    "result_json": result_json,
                    "output_path": str(output_path),
                })
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed_total = int(time.time() - sheet_start)
                analyze_status_map[sheet] = f"분석 완료 ({elapsed_total}초)"
                timer_ph.success(f"✅ [{sheet}] 완료! (총 {elapsed_total}초 소요)")
                append_inspect_log(f"시트 분석 완료 | sheet={sheet} | elapsed={elapsed_total}s", log_path=log_path)

            except Exception as e:
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed = int(time.time() - sheet_start)
                st.error(f"[{sheet}] 처리 중 오류: {e}")
                timer_ph.error(f"❌ [{sheet}] 오류 ({elapsed}초 후)")
                append_inspect_log(f"시트 분석 오류 | sheet={sheet} | elapsed={elapsed}s | error={e}", log_path=log_path)
                if convert_status_map.get(sheet) == "변환 중":
                    convert_status_map[sheet] = "변환 오류"
                else:
                    analyze_status_map[sheet] = "분석 오류"
            finally:
                st.session_state["convert_status"] = convert_status_map
                st.session_state["analyze_status"] = analyze_status_map
                render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

        st.session_state["analysis_results"] = analysis_results
        append_inspect_log(f"일치도 분석 실행 종료 | 성공 시트 수={len(analysis_results)}", log_path=log_path)
        if analysis_results:
            st.session_state["selected_sheet"] = analysis_results[0]["sheet"]

    # ── 결과 표시 ─────────────────────────────────────────
    results = st.session_state.get("analysis_results", [])
    if not results:
        return

    st.subheader("대본별 일치도 분석 결과")
    if "selected_sheet" not in st.session_state:
        st.session_state["selected_sheet"] = results[0]["sheet"]

    per_row = 4
    for start in range(0, len(results), per_row):
        chunk = results[start:start + per_row]
        cols = st.columns(per_row)
        for idx, item in enumerate(chunk):
            with cols[idx]:
                is_selected  = st.session_state["selected_sheet"] == item["sheet"]
                border_color = "#9ca3af" if is_selected else "rgba(128,128,128,0.2)"
                bg_color     = "rgba(107,114,128,0.14)" if is_selected else "var(--secondary-background-color)"
                st.markdown(
                    f"""
                    <div style="border:2px solid {border_color}; border-radius:12px; padding:10px 14px;
                                background:{bg_color}; margin-bottom:4px;">
                      <div style="font-size:14px; font-weight:700; color:var(--text-color);
                                  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                        {html.escape(item['sheet'])}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("선택", key=f"sel_{item['sheet']}", use_container_width=True):
                    st.session_state["selected_sheet"] = item["sheet"]
                    st.rerun()

    selected_sheet = st.session_state.get("selected_sheet", results[0]["sheet"])
    selected       = next((x for x in results if x["sheet"] == selected_sheet), results[0])
    result_json    = selected["result_json"]

    st.markdown("---")
    rows = build_comparison_rows(
        result_json=result_json,
        script_json=selected.get("script_json"),
        summary_manual=result_json.get("summary_manual"),
    )
    filter_col, btn_col = st.columns([3, 1])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with filter_col:
        verdict_filter = st.radio(" ", options=["전체", "일치", "불일치"], horizontal=True, key="verdict_filter")
    with btn_col:
        if rows:
            st.markdown("<div style='height:22px;'></div>", unsafe_allow_html=True)
            source_file_name = safe_name(selected.get("source_file_name") or "원본파일")
            st.download_button(
                label="📥 CSV 다운로드",
                data=rows_to_csv(rows),
                file_name=f"비교결과_{ts}_{source_file_name}_{safe_name(selected['sheet'])}.csv",
                mime="text/csv",
                use_container_width=True,
            )
    render_comparison_table(rows, verdict_filter)

    st.markdown("---")
    st.subheader("요약 정보")
    summary_script = result_json.get("summary_script") or result_json.get("summary", "-")
    summary_manual = summary_manual_to_text(result_json.get("summary_manual"))
    match_rate_raw = selected.get("match_rate")
    if isinstance(match_rate_raw, dict):
        match_rate = match_rate_raw.get("rate")
    else:
        match_rate = match_rate_raw
    rate_col, left, right = st.columns(3)
    with rate_col:
        if match_rate is not None:
            color = "#065f46" if match_rate >= 70 else "#991b1b"
            bg = "#d1fae5" if match_rate >= 70 else "#fee2e2"
        else:
            color = "var(--text-color)"
            bg = "var(--secondary-background-color)"
        rate_text = f"{match_rate}%" if match_rate is not None else "-"
        st.markdown(
            f"""
            <div style="border:1px solid rgba(128,128,128,0.2); border-radius:14px; padding:14px;
                        background:{bg}; text-align:center; height:100%;">
              <div style="font-size:14px; color:{color}; opacity:0.85; margin-bottom:6px;">일치율</div>
              <div style="font-size:32px; font-weight:800; color:{color};">{rate_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with left:
        render_info_block("판매대본 요약", summary_script)
    with right:
        render_info_block("상품설명서 요약", summary_manual)


if __name__ == "__main__":
    main()
