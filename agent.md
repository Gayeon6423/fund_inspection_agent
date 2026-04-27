# VIBE Coding Guide

이 문서는 이 저장소에서 AI(코딩 에이전트)가 추가 개발할 때 따라야 할 기본 가이드입니다.

## 1. 프로젝트 목적
- inspect_agent: 펀드 판매대본(Excel/JSON)과 상품설명서(PDF)를 비교해 일치도와 불일치 근거를 생성한다.
- generate_agent
- 결과는 사람이 검토 가능한 형태(JSON/테이블/로그)로 남긴다.

## 2. 핵심 디렉토리
- `inspect_agent/app.py`: Streamlit UI
- `inspect_agent/api_server.py`: FastAPI 서버, 요청 처리, 결과 저장, 일자별 로그 기록
- `inspect_agent/fund_core.py`: LLM 호출/파싱/비교 공통 로직
- `inspect_agent/excel_json/excel_to_json.py`: Excel -> JSON 변환
- `data/output_excel_json`: 변환 산출물
- `data/output_inspect_agent`: 분석 결과(JSON)
- `data/log`: 서버 실행 로그(`YYYY-MM-DD.log`)

## 3. 개발 원칙
- 기존 입출력 스키마를 깨지 않는다.
- "일치/불일치" 판정 값은 기존 포맷을 유지한다.
- 예외 메시지는 사용자에게 원인 파악이 가능하도록 구체적으로 작성한다.
- 경로는 가능하면 `Path` 기반으로 처리하고 하드코딩을 피한다.

## 4. 변경 시 체크리스트
- 의존성은 `requirements.txt` 기준으로 관리한다.
- UI 변경 시 "일치도 분석 / 프롬프트 수정 / 데이터 / 로그" 메뉴 동작을 모두 점검한다.
- 로그/결과 파일 저장 경로가 README와 일치하는지 확인한다.
- 신규 환경변수 추가 시 `.env_example`과 README에 반영한다.

## 5. 실행/검증
- INSPECT AGENT API 서버: `uvicorn inspect_agent.api_server:app --reload --port 8000`
- INSPECT AGENT 웹 UI: `streamlit run inspect_agent/app.py`
- INSPECT AGENT 테스트 스크립트: `python inspect_agent/api_server_test.py`

## 6. AI 에이전트 작업 방식
- 작은 단위로 수정하고, 영향 파일을 함께 점검한다.
- 파싱/스키마 관련 변경 시 실패 케이스를 먼저 확인한다.
- 사용자 요청이 모호하면 기존 UX/출력 포맷을 우선 보존하는 방향으로 구현한다.
