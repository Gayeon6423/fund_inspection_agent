# 펀드 판매대본 점검 시스템

판매대본 Excel과 상품설명서 PDF를 비교해 **시트별 일치율**과 **항목별 일치/불일치 근거**를 확인하는 프로젝트입니다.

## 핵심 기능
- Excel 판매대본을 JSON으로 변환 (`excel_json/excel_to_json.py`)
- Claude API 기반 비교 분석 (`agent/fund_core.py`, `agent/api_server.py`)
- Streamlit 웹 UI 메뉴 제공
  - `일치도 분석`: 업로드/시트선택/상태확인/결과조회
  - `프롬프트 수정`: 프롬프트 조회/저장/다운로드
  - `데이터 및 로그`: `uploads_local`/`output_agent` 파일 조회
- 기본 프롬프트(.env의 `SYSTEM_PROMPT_VERSION`) + 업로드 프롬프트 오버라이드 지원
- API 서버 단계별 진행 로그(어디서 멈췄는지 추적 가능)

## 폴더 구조
```text
.
├── agent/                      ← 메인 애플리케이션
│   ├── app.py                  ← Streamlit 웹 UI (사용자 인터페이스)
│   ├── api_server.py           ← FastAPI REST API 서버
│   ├── fund_core.py            ← 공유 비즈니스 로직 (LLM 호출, 비교 계산)
│   ├── api_server_test.py      ← API 서버 테스트
│   └── prompt/                 ← 시스템 프롬프트 버전 관리
│       ├── system_prompt_v1.txt ~ v11.txt
│
├── excel_json/                 ← Excel → JSON 변환 모듈
│   └── excel_to_json.py        ← xlsx 파싱 및 구조화
│
├── data/                       ← 데이터 저장소
│   ├── uploads_external/       ← 웹 UI에서 업로드된 파일
│   ├── uploads_local/          ← 로컬에서 입력된 파일
│   ├── output_excel_json/      ← Excel → JSON 변환 결과물
│   └── output_agent/           ← LLM 분석 결과물 (JSON)
│
├── legacy/                     ← 구버전 코드 (참고용 보관)
│   ├── agent_test.py
│   ├── api_client_test copy.py
│   └── api_server copy.py
│
├── .env / .env_example         ← API 키, 모델 설정
├── pyproject.toml / uv.lock    ← 패키지 의존성 (uv 관리)
└── requirements.txt            ← fastapi, uvicorn, streamlit, python-dotenv
```
### 데이터 흐름
```text

Excel 판매대본
    └─→ excel_to_json.py (xlsx 파싱)
            └─→ output_excel_json/ (JSON 저장)
                    └─→ fund_core.py (LLM 호출: Claude)
                            └─→ output_agent/ (비교 결과 JSON)
                                    └─→ app.py (Streamlit 결과 표시)

```

### LLM 응답 포맷
각 판매단계 KEY의 값은 3원소 배열을 출력합니다:
- `value[0]`: "일치" 또는 "불일치" (판정)
- `value[1]`: 설명서 내용
- `value[2]`: 일치/불일치 근거

예시:
```json
{
  "위험등급": ["불일치", "p.4: 6등급 매우낮은위험", "위험등급 수치가 상이함(대본 5등급 ↔ 설명서 6등급)"],
  "환매수수료": ["일치", "p.5: 환매수수료 없음", "유동성/환매 관련 핵심 사실이 설명서와 동일"]
}
```

## 1) 설치
```bash
pip install -r requirements.txt
```

또는 uv를 사용하는 경우:
```bash
uv sync
```

## 2) 환경변수 설정

### 웹 UI (Streamlit) 환경변수
`.streamlit/secrets.toml` 파일에 설정:

```toml
API_KEY="sk-ant-..."
LLM_MODEL="claude-sonnet-4-6"

# Agent configuration
SYSTEM_PROMPT_VERSION="system_prompt_v11"

# Agent Input
INPUT_SCRIPT_FILE="판매대본.json"
INPUT_MANUAL_FILE="상품설명서.pdf"
```

### API 서버 환경변수 (로컬 실행 시)
`.env` 파일 생성:

```env
API_KEY="sk-ant-..."
LLM_MODEL="claude-sonnet-4-6"

# Agent configuration
SYSTEM_PROMPT_VERSION="system_prompt_v11"

# Agent Input (로컬 실행 시)
INPUT_SCRIPT_FILE="판매대본.json"
INPUT_MANUAL_FILE="상품설명서.pdf"
```

## 3) Excel -> JSON 변환
스크립트: `excel_json/excel_to_json.py`

동작:
- `단계` / `예시` 컬럼 기반 변환
- 병합 셀로 이어진 다중 행 예시 누적
- 현재 필터: `설명서 교부`, `설명 의무` 포함 단계만 저장
- 기본 출력 폴더: `data/output_excel_json`
- 출력 파일명: `YYYYMMDD_HHMMSS_원본파일명_시트명.json`

예시:
```bash
# 사모펀드
python excel_json/excel_to_json.py "사모_판매대본_라이프META일반사모투자신탁 제2호.xlsx" --sheets "사모펀드(내점)" "사모펀드(방문)"

# 공모펀드
python excel_json/excel_to_json.py "공모펀드_판매대본_한국투자JPMorgan.xlsx" --sheets "공모펀드(내점)" "공모펀드(유선)" "공모펀드(방문)"
```

## 4) API 서버 실행 (FastAPI)
```bash
uvicorn agent.api_server:app --reload --port 8000
```

주요 엔드포인트:
- `GET /health`
- `POST /v1/agent/fund/ask`
- Swagger: `http://localhost:8000/docs`

### 진행 로그 확인
`api_server.py`는 요청마다 `request_id`를 생성해 단계별 로그를 출력합니다.

로그 단계 예:
- 요청 수신
- 입력 컨텐츠 구성 시작/완료
- 판매대본/설명서 파일 로드
- Claude API 요청 시작/완료
- 응답 JSON 파싱 시작/완료
- 결과 저장 시작/완료
- 요청 처리 완료/실패(소요시간)

중간에 멈추면 로그에서 **어느 단계에서 실패했는지** 바로 확인 가능합니다.

## 5) 웹 UI 실행 (Streamlit)
```bash
streamlit run agent/app.py
```

UI 기능:
- 메뉴 선택: `일치도 분석` / `프롬프트 수정` / `데이터 및 로그`
  - `일치도 분석`
    - (기본) `.env`의 `SYSTEM_PROMPT_VERSION` 파일 사용
    - (선택) 사용자 프롬프트 파일 업로드 시 해당 내용으로 분석 오버라이드
    - 판매대본 Excel 업로드
    - 설명서 PDF 업로드
    - 시트 선택
    - 선택 시트 수 확인
    - 변환 상태 / 분석 상태 확인
  - `프롬프트 수정`
    - 좌측: `agent/prompt` 파일 선택 후 내용 조회
    - 우측: 파일명/내용 입력 후 저장
    - 우측: 입력한 내용 txt 다운로드
  - `데이터 및 로그`
    - 좌측: `data/uploads_local` 파일 선택
      - xlsx: 시트 선택 + 표 확인, 파일 다운로드
      - json/csv: 내용 미리보기
      - pdf: 다운로드 + 미리보기
    - 우측: `data/output_agent` 로그 파일 선택 후 확인
- 본문에서
  - 항목별 일치 비교 표
  - 판정 필터(전체/일치/불일치)
  - 요약 분석(category, summary)
- 분석 중 JSON 파싱 실패 시 최대 3회 자동 재시도

업로드된 파일은 `data/uploads_external/<실행시각>/원본파일명` 형식으로 저장됩니다.

주의:
- 업로드 파일은 반드시 **복호화된 파일**이어야 합니다.

## 6) 주요 함수 및 구조

### `fund_core.py` - 핵심 로직

- **`call_llm_compare(script_json, manual_pdf_bytes, model, api_key, system_prompt)`**
  - 판매대본 JSON + 설명서 PDF를 Claude API로 전송
  - LLM이 각 항목을 검증해 응답 반환

- **`parse_json_from_text(answer_text)`**
  - LLM 응답 텍스트에서 JSON 블록 추출
  - JSON 파싱 후 dict 반환

- **`calc_match_rate(result_json)`**
  - 비교 결과에서 일치율(정수 %) 계산
  - 반환: `int | None`

- **`build_comparison_rows(result_json, script_json=None, summary_manual=None)`**
  - UI 테이블용 행 리스트 생성
  - 3원소 포맷/구버전 포맷을 함께 처리
  - 행 예시:
    - `{"항목": key, "판정": "...", "설명서": "...", "근거": "..."}`

### `app.py` - 웹 UI (Streamlit)

**일치도 분석 페이지 추가 기능:**
- CSV 다운로드: 비교 결과를 CSV로 내보내기
- 판정 필터: "전체"/"일치"/"불일치"

## 7) API 테스트 스크립트
```bash
python agent/api_server_test.py
```

## 8) 문제 해결

### Streamlit 실행 오류
- **`python-dotenv could not parse statement` / `Unbalanced quotes`**
  - `.streamlit/secrets.toml` 파일 확인
  - 모든 문자열이 **큰따옴표(`"`)로 일관되게** 작성되었는지 확인
  - 작은따옴표(`'`)는 TOML 파싱 오류 발생

### 일반 오류
- **`ModuleNotFoundError: excel_json`**
  - 프로젝트 루트에서 `streamlit run agent/app.py` 형태로 실행

- **Claude API 호출 실패**
  - `.streamlit/secrets.toml` 또는 `.env`의 `API_KEY` 확인
  - 모델명 확인 (`claude-sonnet-4-6` 권장)
  - API 서버 로그의 `request_id` 기준으로 실패 단계 확인

- **LLM 응답 파싱 실패**
  - 시스템 프롬프트 버전 확인 (`SYSTEM_PROMPT_VERSION` 설정값)
  - 업로드 프롬프트 사용 시 JSON 출력 규칙 포함 여부 확인

- **PDF 미리보기가 보이지 않음**
  - 브라우저 보안 정책/환경 제약일 수 있으므로 우선 다운로드로 확인
  - 가능한 최신 브라우저에서 재확인
