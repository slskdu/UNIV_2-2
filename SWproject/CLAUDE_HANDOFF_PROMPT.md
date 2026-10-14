# Claude 작업 이어받기 프롬프트

아래 내용을 Claude Code에 붙여 넣고 이 저장소 작업을 이어가세요.

---

너는 `C:\UNIV_2-2\SWproject`의 기존 토스증권 랭킹 프로젝트를 이어서 작업하는 코딩 에이전트다. 사용자는 한국어로 소통한다. 먼저 `chat_log.md`와 프로젝트 안내 문서를 읽고 현재 코드와 사용자의 최신 요청을 기준으로 작업하라. 이전 기록만 믿고 서버 상태나 화면 상태가 그대로라고 단정하지 말고, 필요한 경우 API 응답과 실제 화면을 다시 확인하라.

## 작업 원칙

- 기존 구조와 코딩 스타일을 따르고 요청 범위에 필요한 최소한만 수정한다.
- 작업 전 `git status`를 확인한다. 사용자의 기존 변경사항을 덮거나 되돌리지 않는다.
- `.env` 및 자격 증명을 출력·복사·커밋하지 않는다. `.env.example`은 템플릿으로만 사용한다.
- 코드나 UI 문제는 해당 API 응답, 로그, DOM을 비교해 원인을 확인한 뒤 수정한다. 확인하지 않은 동작을 검증했다고 말하지 않는다.
- 변경 후 수정한 파일에 대해 문법/진단 및 가능한 좁은 테스트를 실행하고 결과를 보고한다.
- 별도 자동화 테스트는 아직 부족한 상태이므로, 관련 기존 테스트/설정을 먼저 찾고 최소한의 회귀 테스트를 추가한다.

## 프로젝트 개요

토스증권 Open API를 사용하는 FastAPI 서비스다. 백그라운드 수집기가 KR/US 상승률·하락률·거래대금 랭킹을 모아 Redis에 저장하고, API와 표 화면이 이를 제공한다. 분야 분석과 선택형 AI 요약도 포함한다.

주요 파일:

- `app/main.py`: FastAPI, Redis 연결 확인, 수집기 lifespan
- `app/config.py`: `.env` 기반 설정. `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` 필수
- `app/auth.py`: OAuth client-credentials 토큰 발급/캐시
- `app/toss_api.py`: 토스 랭킹 및 종목 기본정보 API 클라이언트
- `app/collector.py`: KR/US 랭킹 수집 및 종목명·분야 정보 보강
- `app/redis_client.py`: Redis 캐시 읽기/쓰기
- `app/api/routes.py`: `/api/v1/market-trend`, `/api/v1/market-analysis`, `/market-trend` 웹 화면
- `app/analytics.py`, `app/ai_summary.py`, `app/sector_map.py`, `sector_map.json`: 분야 분석, 요약, 매핑
- `raw_data_viewer.py`: 별도 원본 JSON 확인용 앱
- `chat_log.md`: 이전 작업 내역 및 재개 기록

토스 REST 호스트는 `https://openapi.tossinvest.com`이다. 랭킹 응답은 대체로 `result.rankings` 아래에 있으며, 원본 랭킹에는 종목명 대신 `symbol`만 들어올 수 있다. 메인 화면은 `/api/v1/stocks` 종목 마스터 응답을 수집 단계에서 붙여 종목명을 표시한다. 원본 화면에 종목명이 없는 것은 의도된 동작이며, 원본 화면과 메인 가공 화면을 혼동하지 말 것.

## 실행 및 점검

Windows 명령 프롬프트에서 프로젝트 디렉터리 기준:

```cmd
cd /d C:\UNIV_2-2\SWproject
docker compose up -d redis
docker compose ps
py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 9000
```

`.env`가 없으면 `.env.example`을 참고해 별도로 만들고, 실제 토스 client ID/secret을 안전하게 입력해야 한다. 메인 서버는 Redis 연결이 필요하다. Uvicorn 프로세스가 실행되는 터미널을 닫으면 해당 서버도 종료된다.

메인 화면/API:

- `http://127.0.0.1:9000/health`
- `http://127.0.0.1:9000/docs`
- `http://127.0.0.1:9000/api/v1/market-trend?market=all`
- `http://127.0.0.1:9000/api/v1/market-trend?market=kr`
- `http://127.0.0.1:9000/api/v1/market-analysis?market=all`
- `http://127.0.0.1:9000/market-trend`

별도 터미널에서 원본 데이터 뷰어를 실행할 때:

```cmd
cd /d C:\UNIV_2-2\SWproject
py -m uvicorn raw_data_viewer:app --reload --host 127.0.0.1 --port 9100
```

원본 뷰어 주소는 `http://127.0.0.1:9100/`이다. 이 화면은 검은 배경에 토스 랭킹 JSON만 보여주며, 이름 보강이나 정규화를 하지 않는다. 시장/종류는 쿼리로 선택할 수 있다. 예: `/?market=US&kind=TOP_LOSERS`.

## 최근 UI 변경 및 확인 사항

- `app/api/routes.py`의 표 화면 순서는 AI 요약 → 강세/주의 분야 카드 → 종목 랭킹 → 상세 분야 표다. 상세 분야 표가 종목 랭킹을 밀어내지 않게 배치했다.
- 종목 랭킹 첫 행의 종목명 예시(이전 실제 확인): 통합 `바이오 키 인터내셔널`, 국장 `빅웨이브로보틱스`. 분야 값 `미분류`는 별도의 분야 열에 표시된다.
- `app/collector.py` 종목명 보강 코드의 들여쓰기 오류를 수정한 기록이 있다. 이후 문법 오류가 재발하면 먼저 확인한다.
- `findValue()`는 null/빈 값을 건너뛰도록 수정되어 있다.
- 2026-10-06 확인 시점에는 9000/9100의 로컬 HTTP 요청이 연결 거부였다. 이는 서버가 종료됐을 때의 결과이지 코드 실패의 증거가 아니다. 작업 시작 시 `/health`와 포트 상태를 다시 확인할 것.
- 브라우저에는 이전 `ERR_CONNECTION_REFUSED` 이벤트가 남을 수 있으므로 현재 요청 결과와 구분한다. UI 데이터가 비어 보이면 최신 화면 새로고침, 선택 탭, 응답 JSON, 서버 수집 로그를 대조한다.

## 이어서 진행할 기존 목표

1. 현재 서버/Redis/.env 상태를 비밀값 노출 없이 점검한다.
2. 사용자의 다음 요청을 먼저 해결한다. 요청이 없으면 `sector_map.json`의 분야와 가중치를 공식 기업 자료로 검토하는 작업 및 테스트 코드 추가를 후보로 삼되, 범위를 임의로 확대하지 않는다.
3. 분야 가중 분석은 `WEIGHTED_ANALYSIS_ENABLED=false`가 기본이다. 근거 없이 켜지 말고 변경 전후 결과를 검토한다.
4. 랭킹 API가 HTTP 503이면 빈 Redis/수집 실패/실행 상태를 확인한다. 토스 API의 401/403은 자격 증명, 접근 권한, 허용 IP 설정을 로그에서 확인한다. API 키를 사용자에게 채팅으로 보내 달라고 하지 않는다.

먼저 저장소 상태와 `chat_log.md`의 최신 항목을 확인한 다음, 현재 사용자 요청을 한 문장으로 요약하고 직접 관련된 코드만 살펴 진행하라.

---
