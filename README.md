# 펀드 판매대본 점검/생성 시스템

판매대본 점검(`inspect_agent`)과 판매대본 생성(`generate_agent`)을 제공하는 프로젝트입니다.

## 핵심 기능
- `inspect_agent`
  - 판매대본 Excel + 상품설명서 PDF 비교
  - 항목별 일치/불일치, 근거, 일치율 제공
  - Streamlit UI + FastAPI API 제공
- `generate_agent`
  - 상품설명서 PDF 기반 판매대본 생성
  - Streamlit UI + FastAPI API 제공
- 프롬프트 버전 관리
  - 프롬프트는 `.md` 기준으로 사용

## 폴더 구조
```text
.
├── inspect_agent/
│   ├── app.py
│   ├── api_server.py
│   ├── fund_core.py
│   ├── api_server_test.py
│   └── prompt/
├── generate_agent/
│   ├── app.py
│   ├── api_server.py
│   ├── fund_core.py
│   ├── api_server_test.py
│   └── prompt/
├── data/
│   ├── log/
│   ├── uploads_external/
│   ├── uploads_local/
│   ├── output_excel_json/
│   ├── output_inspect_agent/
│   └── output_generate_agent/
├── .env / .env_example
├── pyproject.toml
└── requirements.txt
```

## 설치
```bash
pip install -r requirements.txt
```

## 환경변수
`.env` 와 Streamlit Cloud `secrets.toml`에 설정합니다.

```env
API_KEY="sk-ant-..."
LLM_MODEL="claude-haiku-4-5-20251001" # "claude-sonnet-4-6"

# Inspect
INSPECT_SYSTEM_PROMPT_VERSION="inspect_system_prompt_v11"

# Generate
GENERATE_SYSTEM_PROMPT_VERSION="generate_system_prompt_v2"
```

주의:
- Streamlit Cloud에서는 `secrets` 값이 우선됩니다.
- 로컬에서는 `.env` 값이 사용됩니다.

## 실행 방법

### 1) Inspect Streamlit
```bash
streamlit run inspect_agent/app.py
```

기능:
- 일치도 분석
- 프롬프트 수정/저장(`.md` 기본)
- 데이터 조회
- 로그 조회

결과:
- 분석 결과 JSON: `data/output_inspect_agent/`
- CSV 다운로드 파일명:
  - `비교결과_{YYYYMMDD_HHMMSS}_{원본파일명}_{시트명}.csv`

### 2) Generate Streamlit
```bash
streamlit run generate_agent/app.py
```

결과 저장(`data/output_generate_agent/`):
- 로컬 실행:
  - `local_{YYYYMMDD_HHMMSS}_생성결과_{설명서파일명}_{prompt_tag}.json`
- Streamlit Cloud 실행:
  - `web_{YYYYMMDD_HHMMSS}_생성결과_{설명서파일명}_{prompt_tag}.json`

동일 규칙으로 CSV도 저장/다운로드됩니다.

`prompt_tag` 예:
- `GENERATE_SYSTEM_PROMPT_VERSION=generate_system_prompt_v2` → `prompt_v2`

### 3) Inspect API 서버
```bash
uvicorn inspect_agent.api_server:app --reload --port 8000
```

주요 엔드포인트:
- `GET /health`
- `POST /v1/agent/fund/ask`

### 4) Generate API 서버
```bash
uvicorn generate_agent.api_server:app --reload --port 8001
```

주요 엔드포인트:
- `GET /health`
- `POST /v1/agent/fund/generate-script`

## 로그 규칙

### Streamlit 앱 로그
- Inspect: 실행 1회당 1파일
  - `data/log/inspect_{YYYYMMDD_HHMMSS}.log`
- Generate: 실행 1회당 1파일
  - `data/log/generate_{YYYYMMDD_HHMMSS}.log`

### API 서버 로그
- 서버 로거 기준 일 단위 파일
  - 예: `data/log/inspect_YYYY-MM-DD.log`, `generate_YYYY-MM-DD.log`

## Excel → JSON 변환 (Inspect 내부 사용)
스크립트:
- `inspect_agent/excel_json/excel_to_json.py`

예시:
```bash
python inspect_agent/excel_json/excel_to_json.py "../../data/uploads_local/사모_판매대본_라이프META일반사모투자신탁 제2호.xlsx" --sheets "사모펀드(내점)"
```

## 테스트
```bash
python inspect_agent/api_server_test.py
python generate_agent/api_server_test.py
```

## 트러블슈팅
- `API_KEY` 누락: `.env` 와 `secrets.toml` 확인
- 모델 변경이 반영되지 않음:
  - Cloud 실행 중이면 `secrets`의 `LLM_MODEL`을 수정
- 프롬프트 파일 로드 실패:
  - `INSPECT_SYSTEM_PROMPT_VERSION` / `GENERATE_SYSTEM_PROMPT_VERSION` 값과 `prompt/*.md` 파일명 확인
- PDF 업로드 오류:
  - 복호화된 PDF인지 확인
