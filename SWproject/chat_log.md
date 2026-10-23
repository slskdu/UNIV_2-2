# 프로젝트 대화 기록

## 실행 방법 (현재 기준)

```cmd
cd /d C:\UNIV_2-2\SWproject
docker compose up -d redis
py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 9000
```

- 화면: `http://127.0.0.1:9000/market-trend` (탭: 시장 분석 · 시장 랭킹 · 분야 흐름·기사 · 기간별 주가 흐름)
- API 문서: `http://127.0.0.1:9000/docs`
- 테스트: `py -m unittest discover -s tests`
- `.env` 필수: `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`. 선택: `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`, `AI_FALLBACK_MODELS`(값 예시는 `.env.example`). `.env`를 바꾸면 서버를 재시작해야 한다.
- 아래 날짜별 기록 중 9월 기록의 8000번 포트, `미분류` 처리 등은 당시 기준이며 지금은 바뀌었다. 최신 상태는 맨 아래 `현재 상태와 다음 재개 지점`을 본다.

## 2026-09-23

### 오늘의 핵심 대화

- 토스증권 Open API 기반 FastAPI 백엔드 구조를 공식 문서 기준으로 재구성했다.
- 공식 문서 주소는 `https://developers.tossinvest.com/docs`이며, REST API 기본 주소는 `https://openapi.tossinvest.com`이다.
- OAuth 2.0 Client Credentials Grant 방식으로 토큰을 발급받고, 랭킹 API에서 국내 주식 상승률, 하락률, 거래대금 랭킹을 수집한다.
- FastAPI는 수집된 데이터를 Redis에 저장하고, 프론트엔드 또는 Chrome Extension에는 `/api/v1/market-trend`로 제공한다.
- `/docs`는 사용자용 주식 화면이 아니라 FastAPI API를 확인하고 테스트하는 Swagger UI라는 점을 확인했다.

### 발생한 에러와 원인

#### 1. `127.0.0.1:8000` 연결 거부

증상:

- `http://127.0.0.1:8000/health` 연결 거부
- `http://127.0.0.1:8000/docs` 연결 거부

원인:

- Uvicorn이 서버를 열기 전에 설정 로딩 단계에서 종료됐다.
- `.env`에 이전 버전의 `TOSS_ACCESS_TOKEN`만 있었고, 현재 코드가 요구하는 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`이 없었다.

해결:

```env
TOSS_CLIENT_ID=실제_client_id
TOSS_CLIENT_SECRET=실제_client_secret
```

필수 인증 키를 `.env`에 추가한 뒤 FastAPI import와 서버 기동을 확인했다.

#### 2. 토큰 요청 `403 edge-blocked`

증상:

```text
POST https://openapi.tossinvest.com/v1/oauth2/token
403 Forbidden
code: edge-blocked
```

원인:

- `.env`의 `TOSS_API_BASE_URL`에 `/v1`이 포함되어 토큰 주소가 잘못 조합됐다.

잘못된 값:

```env
TOSS_API_BASE_URL=https://openapi.tossinvest.com/v1
```

수정한 값:

```env
TOSS_API_BASE_URL=https://openapi.tossinvest.com
```

이후 정상 호출 주소는 다음과 같다.

```text
POST https://openapi.tossinvest.com/oauth2/token
GET https://openapi.tossinvest.com/api/v1/rankings
```

#### 3. 랭킹 데이터 없음 `HTTP 503`

원인:

- Redis가 비어 있었던 것이 아니라, 기존 Uvicorn 프로세스가 이전 `.env` 설정을 계속 사용하고 있었다.
- 서버를 재시작해 최신 환경변수를 다시 읽도록 했다.

해결 후 확인 결과:

```text
토큰 발급: HTTP 200
상승 랭킹: 50개 수집
하락 랭킹: 50개 수집
거래대금 랭킹: 50개 수집
/api/v1/market-trend: HTTP 200
source: redis
```

### 수정된 코드 및 설정 내역

#### FastAPI 및 애플리케이션 구조

- `app/main.py`
  - FastAPI 앱 생성
  - CORS 설정
  - lifespan에서 Redis 연결 확인
  - 백그라운드 랭킹 수집기 실행 및 종료 처리
  - `/health` 엔드포인트 제공
- `app/api/routes.py`
  - `/api/v1/market-trend` 추가
  - Redis의 최신 랭킹을 반환
  - 캐시 데이터가 없으면 HTTP 503 반환
- `app/__init__.py`, `app/api/__init__.py`
  - Python 패키지 표시 파일 추가

#### 인증 및 토스 API 클라이언트

- `app/auth.py`
  - `/oauth2/token`에 form-urlencoded 방식으로 요청
  - access token 메모리 캐시
  - 만료 시 자동 재발급
  - 동시 토큰 요청 방지를 위한 asyncio lock 사용
- `app/toss_api.py`
  - 공식 랭킹 경로 `/api/v1/rankings` 사용
  - `Authorization: Bearer {access_token}` 헤더 사용
  - `type`, `marketCountry`, `duration`, `excludeInvestmentCaution`, `count` 파라미터 전송
  - 401 응답 시 토큰 재발급 후 1회 재요청
  - 429 응답 시 `Retry-After`만큼 대기 후 재요청

#### 설정 수정

- `app/config.py`
  - `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` 필수화
  - `TOSS_API_BASE_URL` 기본값을 `https://openapi.tossinvest.com`으로 설정
  - 랭킹 수량 파라미터를 `limit`에서 공식 문서의 `count`로 변경
  - `count`를 1~100 범위로 자동 보정하는 `safe_ranking_count` 추가
  - `excludeInvestmentCaution` 설정 추가
- `.env.example`
  - 현재 코드와 공식 문서에 맞는 환경변수 이름으로 정리
- `.env`
  - 실제 실행에 필요한 client ID와 client secret 키 추가
  - API 기본 주소에서 잘못된 `/v1` 제거

#### Redis 및 수집기

- `app/redis_client.py`
  - 상승, 하락, 거래대금 데이터를 JSON으로 Redis에 저장
  - 캐시 TTL 기본값 30초
  - `asyncio.gather`로 세 종류 캐시 조회
- `app/collector.py`
  - 20초 주기 백그라운드 수집
  - `TOP_GAINERS`, `TOP_LOSERS`, `MARKET_TRADING_AMOUNT` 수집
  - 요청 사이에 0.25초 간격 적용
  - 일부 요청 실패 시 성공한 데이터만 저장

#### 실행 환경 파일

- `requirements.txt` 추가
  - FastAPI, Uvicorn, httpx, redis, pydantic-settings
- `docker-compose.yml` 추가
  - Redis 7 컨테이너
  - 호스트 포트 `6379` 사용
- `.gitignore`
  - `.env`, 캐시, 가상환경, 인증서 및 비밀 파일 제외

### 현재 실행 및 검증 상태

- Redis 포트 `6379` 실행 확인
- FastAPI 포트 `8000` 실행 확인
- `/health` 응답 `HTTP 200`
- `/docs` 응답 `HTTP 200`
- `/api/v1/market-trend` 응답 `HTTP 200`
- Redis에 상승/하락/거래대금 각각 50개 랭킹 저장 확인
- Python 문법 컴파일 및 모듈 import 검증 완료

### 다음 작업 목표

1. `market-trend` 응답을 프론트엔드 전용 형식으로 정리한다.
   - 종목 코드
   - 현재가
   - 기준가
   - 등락률
   - 거래량
   - 거래대금
   - 순위
   - `rankedAt`
   - 수집 시각
2. 프론트엔드 또는 Chrome Extension에서 `/api/v1/market-trend`를 호출해 실제 랭킹 화면을 만든다.
3. 로딩, 빈 캐시, 토큰 오류, Redis 오류 상태를 화면에 표시한다.
4. 테스트 코드를 추가한다.
   - `/health` 테스트
   - Redis 저장/조회 테스트
   - 토큰 캐시 및 401 재발급 테스트
   - 429 재시도 테스트
   - 랭킹 수집 성공/부분 실패 테스트
5. 운영 준비를 진행한다.
   - CORS에 실제 허용 origin만 등록
   - 토스 Open API 허용 IP 등록
   - API 키 노출 여부 확인
   - Uvicorn 다중 worker로 인한 중복 수집 방지
   - 배포용 Redis 및 FastAPI 실행 구성

### 다음 날 작업 재개 지침

다음 날 새 창에서 `chat_log.md`의 마지막 기록을 읽고, 위의 `다음 작업 목표` 중 아직 완료되지 않은 항목부터 이어서 작업한다. 우선 현재 서버와 Redis 실행 상태, `.env`의 API 주소, `/api/v1/market-trend` 응답을 다시 확인한 뒤 코드를 수정한다.

## 2026-09-27 최종 작업 기록

### 웹 표 화면

- `/market-trend` HTML 화면을 FastAPI에 추가했다.
- JSON 응답을 엑셀 형태의 표로 표시하도록 구성했다.
- 상승률·하락률·거래대금 탭과 새로고침, 20초 자동 갱신을 추가했다.
- 토스 응답의 실제 구조인 `result.rankings`, `price.lastPrice`, `price.changeRate`, `tradingVolume`, `tradingAmount`에 맞춰 필드를 수정했다.
- API가 반환하는 소수형 등락률을 백분율로 환산했다.
- 종목 마스터 API `/api/v1/stocks?symbols=...`를 사용해 종목명과 거래소 정보를 추가했다.

### 국장·미장 분리 및 통합

- `marketCountry=KR`, `marketCountry=US`를 각각 수집한다.
- Redis 캐시를 `market:ranking:kr:*`, `market:ranking:us:*`로 분리했다.
- 통합 탭은 KR·US 데이터를 합친 뒤 상승률·하락률·거래대금 기준으로 재정렬한다.
- 통합 순위를 1위부터 다시 부여하고 각 행에 시장을 표시한다.

### 토스 데이터 기준 보정

- 토스 웹 화면과 비교해 거래대금 랭킹은 `MARKET_TRADING_AMOUNT`와 `duration=realtime`을 사용하도록 수정했다.
- 상승률·하락률은 API 제한에 따라 `duration=1d`를 사용한다.
- 투자 유의 종목은 `excludeInvestmentCaution=false`로 포함한다.
- 토스 웹과 API의 `rankedAt`, 조회 시점, 필터 차이로 순위가 달라질 수 있음을 확인했다.

### 분야 분석 및 AI

- `app/analytics.py`에 분야별 평균 등락률, 상승 비율, 거래대금 비중, 분야 점수를 구현했다.
- `GET /api/v1/market-analysis?market=all|kr|us`를 추가했다.
- `app/ai_summary.py`에 선택형 AI 요약을 추가했다.
- `AI_API_KEY`가 없으면 로컬 수치 기반 요약을 사용하고, 키가 있으면 OpenAI 호환 Chat Completions API를 사용한다.
- `sector_map.json`에 세부 분야와 복합 기업 가중치 구조를 추가했다.
- 정확한 사업부문 자료가 준비될 때까지 `WEIGHTED_ANALYSIS_ENABLED=false`로 가중 분석을 보류했다.

### 최종 검증

- Python compile 검사 통과
- FastAPI 라우트 확인:
  - `/health`
  - `/api/v1/market-trend`
  - `/api/v1/market-analysis`
  - `/market-trend`
- 실제 KR·US 랭킹 수집 확인
- 가중 분야 계산 모의 테스트 통과
- AI 키 없이 fallback 요약 테스트 통과

### 다음 재개 지점

1. `sector_map.json`의 분야와 가중치를 공식 기업 자료로 보완한다.
2. `WEIGHTED_ANALYSIS_ENABLED=true` 전환 전 분석 결과를 검토한다.
3. 필요하면 실제 AI API 키를 `.env`에 추가한다.
4. 테스트 코드를 별도 추가한다.

## 2026-09-30 작업 기록

### 종목명 및 화면 배치

- `app/collector.py` 종목명 보강 코드의 들여쓰기 오류를 수정했다. 오류 당시 Uvicorn 하위 프로세스가 앱을 import하지 못해 연결이 거부됐다.
- `app/api/routes.py`의 필드 조회 함수가 `null`이나 빈 문자열에서 멈추지 않고 다음 이름 별칭을 찾도록 했다.
- 종목명 표시에서 이름이 없으면 심볼을 표시하고, 분야·시장 값이 종목명처럼 잘못 노출되지 않도록 했다.
- 현재 화면 순서는 AI 요약 → 강세/주의 분야 카드 → 종목 랭킹 → 상세 분야 표다. 강세/주의 카드는 상단에 유지하고, 긴 상세 분야 표 때문에 종목명이 화면 아래로 밀리지 않도록 랭킹 표를 앞에 배치했다.
- 실제 `http://127.0.0.1:9000/market-trend`에서 통합·국장 탭을 확인했다. 통합에는 미국 종목명, 국장에는 `빅웨이브로보틱스`, `글로벌테크놀로지` 등 이름이 표시됐다. `/health`와 랭킹 API도 HTTP 200을 반환했다.
- 브라우저에 남은 과거 `ERR_CONNECTION_REFUSED`는 해당 시점 서버가 응답하지 않았던 기록이다. 현재 확인 시점에는 9000번 서버와 API가 응답했다.

### 원본 데이터 뷰어

- 새 파일 `raw_data_viewer.py`를 추가했다. 기존 원본 랭킹 API를 직접 호출하며, 정규화나 종목명 보강은 하지 않는다.
- 화면은 검은 배경과 원본 JSON 텍스트만 표시하고, 필드와 값은 바꾸지 않은 채 줄바꿈/들여쓰기만 적용한다.
- 원본 Ranking API 응답에는 종목명 대신 `symbol`이 들어온다. 따라서 이름이 필요한 가공 화면은 종목 마스터 조회로 보강하지만, 원본 뷰어에 이름이 없는 것은 정상이다.
- 원본 뷰어 주소: `http://127.0.0.1:9100/`
- 실행 명령: `py -m uvicorn raw_data_viewer:app --reload --host 127.0.0.1 --port 9100`
- 원본 뷰어도 HTTP 200 및 JSON 응답을 확인했다.

### 다음 작업 재개 지점

1. 먼저 `http://127.0.0.1:9000/health`와 `/api/v1/market-trend?market=kr`로 메인 서버 상태를 확인한다.
2. `/market-trend`에서 통합/국장 이름 표시는 긴 분야 상세 표 아래가 아닌 종목 랭킹 표에서 확인한다. 브라우저 변경사항이 안 보이면 `Ctrl+F5`로 강력 새로고침한다.
3. 이름이 없는 현상이 재발하면 가공 화면(9000)인지 원본 뷰어(9100)인지 먼저 구분한다. 원본 응답은 이름 필드가 없는 것이 정상이며, 메인 화면에서만 종목 마스터 이름 보강 여부를 조사한다.
4. 원본 뷰어가 종료된 경우 위 실행 명령으로 재기동한다. 메인 API 서버와 원본 뷰어는 각각 9000/9100 포트를 사용한다.
5. 기존 미완료 목표인 `sector_map.json` 공식 자료 보완 및 테스트 코드 추가를 이어간다.

## 2026-10-06 작업 기록

### 종목 분야 분류 보강

- 미장 종목은 기존 `sector_map.json`에 미국 티커가 거의 없어 `미분류`가 많았다. `us_sector_map.json`을 새로 추가해 Yahoo Finance 분야를 한국어 대분류로 매핑했다.
- `app/sector_map.py`가 기존 한국 매핑과 미장 매핑을 병합해 읽는다. 파일 수정 시각을 캐시 키로 사용해 JSON 변경도 서버 재시작 없이 반영된다.
- `app/toss_api.py`에 Yahoo 공개 검색의 분야 조회와 프로세스 내 캐시, 동시 조회 제한(4)을 추가했다. `app/collector.py`는 정적 매핑에 없는 미국 일반주를 조회하고, 국내 일반주는 KOSPI `.KS`/KOSDAQ `.KQ` 형식으로 조회한다. ETF/ETN은 토스 `securityType`을 기준으로 자동 분류한다.
- 동적 국내 조회 연결 때 `asyncio.gather()`에 코루틴 생성기를 펼치지 않은 오류가 있어 종목 보강 단계가 건너뛰어진 적이 있었다. `gather(*...)`로 수정했다.
- 2026-10-06 당시 KR 142개 종목의 `미분류`가 90개에서 0개로 줄었고, 480370 씨케이솔루션은 산업재, 351320 넥사다이내믹스는 금융으로 추가했다. Yahoo 분야를 찾지 못한 새 종목은 임의 추정하지 않고 `분야 확인 필요`로 표시한다.
- VJET는 SEC의 2026-10-05 8-K 첨부 보도자료에서 민간항공/전용기 플랫폼 사업을 확인해 산업재로 분류했다. 회사는 기존 생명과학 사업도 유지한다고 공시했다.
- 분야 정보는 Yahoo/공시 기반의 대분류다. 공식 토스 종목 마스터가 사업 분야를 제공하는 것은 아니다.

### 정렬 및 통화 표시

- `app/api/routes.py` 종목 표는 상승률을 등락률 내림차순, 하락률을 등락률 오름차순으로 모든 시장 탭에서 동일하게 정렬한다.
- 단일 시장 거래대금은 해당 시장의 거래대금 내림차순으로 정렬한다. 통합 거래대금은 KRW와 USD를 직접 비교하지 않고 각 시장 원본 순위로 합친다. 표시 순위는 정렬된 화면 순서대로 다시 부여한다.
- 현재가 표시는 KRW에 `원`, USD에 `$`를 붙인다. 통합에서는 행의 통화에 따라 표시한다.
- CSS 진단 및 브라우저 화면에서 통합/국장/미장 표를 확인했다.

### 상세 시장 분석

- 새 `app/market_insights.py`가 상승 확산도, 평균-중앙값 괴리, 거래대금 집중도, 분야별 표본 수를 해석한다. 점수나 기사만으로 원인을 단정하지 않고 표본 한계를 함께 표시한다.
- `app/analytics.py`에 중앙값과 상위 5개 거래대금 비중을 추가했다. 통합 시장의 거래대금 비중은 KR/US 시장별 비중의 동일 가중 평균으로 계산하며, 통화 금액은 섞지 않는다. 총액은 화면에서 KR/US별로 나눠 보여준다.
- `app/news.py`는 선택 시장의 상승/하락/거래대금 상위 종목을 대상으로 최근 30일 Yahoo Finance 관련 기사를 조회한다. 최대 5개 티커 검색, 동시 요청 3개, 응답 기사 최대 8건이다. 관련 기사 검색은 보조 자료이며 주가 변동 원인으로 단정하지 않는다.
- `app/ai_summary.py`는 AI 키가 없을 때도 규칙 기반 해석을 제공한다. 짧은 요약에는 선택 시장명, 상승 비율, 평균/중앙값, 강세/주의 분야를 넣는다. AI가 설정되면 숫자 낭독 대신 확산도·쏠림·분야 표본을 해석하도록 프롬프트를 변경했다.
- 화면의 `분석 실행`은 기본으로 짧은 결론을 보여주고, `자세히 보기`를 누르면 전체 해석 근거, 종목별 상승/하락/거래대금 상위, 분야별 대표 종목 및 기사 링크를 펼친다. `요약 보기`로 접는다.
- 인라인 JS escape 함수의 따옴표 이스케이프 오류를 수정했다. 실제 브라우저에서 초기 데이터 로드, 요약, 상세 펼침/접힘을 확인했다.
- 시장별 요약이 같아 보이는 문제는 brief가 공통 임계값 문장만 보여준 탓이었다. 시장별 요약에 종목 수·상승 비율·평균·중앙값·분야명을 넣도록 수정했고, 당시 API 비교에서 all/KR/US의 종목 수는 291/142/149로 구분됐다.

### 검증 결과와 재개 지점

- 실행 검사: 분석 모듈이 넓은 상승과 평균-중앙값 괴리/거래 집중 사례를 구분하는 합성 검사를 통과했다.
- 실 API 검사: `GET /api/v1/market-analysis?market=all|kr|us&ai=true`가 각각 다른 시장 데이터를 반환했다. 통합 상세 응답은 KR/US 거래대금 비중을 분리해서 반환했다.
- 현재 탭 선택 후 `분석 실행`을 눌러야 해당 시장의 상세 분석을 요청한다. 시장 탭만 바꾸면 기존 상세 리포트가 화면에 남을 수 있으므로 새 시장을 선택한 뒤 다시 분석 버튼을 누른다.
- 정식 자동화 테스트 파일은 아직 없다. 수정된 파이썬 파일의 진단과 API/브라우저 실검증을 수행했다.
- 다음 작업은 시장 해석 규칙에 대한 회귀 테스트를 추가하고, 필요하면 분야별 거래대금 계산이 통합 탭에서 시장 평균 비중이라는 점을 사용자에게 계속 명확히 표시하는 것이다.
- 메인 실행: `py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 9000`; 브라우저: `http://127.0.0.1:9000/market-trend`. Redis가 필요하고 실행 터미널을 닫으면 서버가 종료된다.
- 원본 JSON 화면은 `raw_data_viewer.py` 및 9100번 포트에 별도 존재한다. 원본 랭킹에는 종목명이 없을 수 있으므로 종목명/분야 분석은 9000번 화면을 사용한다.

## 2026-10-13 작업 기록

### 추가 데이터 수집: 공식 섹터·캔들·분야별 기사

- 토스 Open API 스펙(`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`)을 확인해, 그동안 쓰지 않던 캔들(`/api/v1/candles`, `/api/v1/market-indicators/{symbol}/candles`)과 공식 섹터(`/api/v1/sectors`, `/sectors/rankings`, `/sectors/{id}`, `/sectors/{id}/stocks`) API를 `app/toss_api.py`에 추가했다. 공통 GET 처리 `_get_result`가 401 재발급과 429 재시도를 맡는다.
- `app/history.py`: 임의 기간 일봉 수집(200봉 페이지네이션, 최대 8페이지)과 흐름 요약(기간 등락률, 최대 낙폭, 변동성, 최대 5구간의 상승/하락/횡보). 방향 판정 기준은 `일간 변동성 x sqrt(거래일) x 0.25`(최소 1%)다.
- `app/sectors.py`: 대분류 섹터 전체의 1일~1년 등락률과 흐름 단계(상승 지속, 상승 중 단기 조정, 상승 후 꺾임, 반등 전환, 하락 중 반등 시도, 하락 지속). 섹터 랭킹 API는 상위 100개만 주어 하락장에서 약한 분야가 잘리므로, 대분류마다 섹터 상세를 조회한다(약 10초). 그래서 `sector_flow_loop`가 10분마다 미리 갱신해 Redis에 저장한다.
- `app/sector_news.py`: Google 뉴스 RSS로 분야명 + 시총 상위 3종목 기사를 수집한다. 제목에 대상 이름이 없는 기사는 버리고(예: '조선' 검색의 '조선일보'), 비슷한 제목은 하나로 합쳐 `similar_count`를 남긴다.
- `app/news_classifier.py`: 제목 기반 호재/악재/중립 분류와 사건 유형(실적, 수주·계약, 정책·규제, 수급 등 11종) 분류, 논조 요약, 기사 논조와 가격 방향의 일치/괴리 문장. `AI_API_KEY`가 있으면 AI 분류·요약으로 보강하지만 키가 없어 이 경로는 실행해 보지 못했다.

### API와 화면

- 새 API(`app/api/insight_routes.py`): `/api/v1/sector-flow`, `/api/v1/sectors/{id}/report`, `/api/v1/sectors/{id}/trend`, `/api/v1/price-history`, `/api/v1/stock-news`.
- `/market-trend` 화면에 `기간별 주가 흐름`(종목·KOSPI/KOSDAQ, 기간 지정, 차트, 종목 기사)과 `분야 흐름`(분야 표, 분야 클릭 시 세부 분야·구성 종목·기사 논조·지정 기간 근사 지수) 패널을 추가했다. 랭킹 표의 종목을 누르면 주가 흐름이 열린다.
- 지정 기간 분야 흐름은 공식 섹터 지수 캔들이 없어 시총 상위 5종목 동일 가중 지수로 근사한다.

### 검증과 주의점

- `py -m unittest discover -s tests` 13건 통과(분류, 요약, RSS 파싱, 추세, 흐름 단계).
- Docker가 꺼져 있어 Redis 대신 메모리 대역으로 앱을 띄워 실제 토스 데이터로 새 API 5종과 화면 동작(분야 클릭, 기간 지정, 종목 클릭)을 확인했다. 실제 Redis로는 확인하지 않았다.
- Google 뉴스 RSS는 약관상 개인·비상업 용도다. 서비스로 공개하거나 수익화하려면 네이버 검색 API 같은 정식 뉴스 API로 `search_news`를 교체해야 한다.
- 다음 후보: 기존 `분석 실행` 리포트에 공식 섹터 흐름과 기사 논조 연결, 관심 종목 저장과 알림, 투자자별 매매동향(`/stocks/{symbol}/investor-trading`) 추가.

## 2026-10-14 작업 기록

### 화면 구성

- `/market-trend` 상단에 보기 탭 3개(`시장 랭킹`, `분야 흐름·기사`, `기간별 주가 흐름`)를 두었다. 종목을 누르면 주가 흐름 탭으로 넘어간다.
- 주가 흐름 탭에 지수와 거래대금·상승률·하락률 상위 10종목 버튼을 두어 검색 없이 눌러서 본다.
- 흐름 단계(상승 지속 등)는 토스 제공 값이 아니라 토스의 1일·1주·1개월 등락률 부호로 자체 분류한 라벨이다(`app/sectors.py`의 `flow_label`).

### 기사 수집 확대·번역·본문 요약

- 검색어를 늘리고(종목: 이름 + 주가·실적·전망, 분야: `○○주`·업계·산업, 시총 상위 5종목) 상한을 종목 60건, 분야 80건으로 올렸다. 미장 종목은 영문 기사도 수집한다. 목록 위에 호재/악재/중립 필터가 있다.
- `app/translator.py`: 영문 제목과 요약문을 한국어로 번역한다. 키 없는 Google 번역 비공식 경로 2개를 차례로 시도하고 30일 캐시한다. httpx GET은 429로 막혀 한쪽은 POST를 쓴다. 막히면 원문이 그대로 보인다.
- `app/article_summary.py`와 `GET /api/v1/article-summary?url=`: Google 뉴스 링크를 언론사 주소로 풀고 본문을 읽어 요약한다. 본문에서 제목과 겹치는 낱말·빈도·앞쪽 문장 기준으로 3문장을 고르는 추출 요약이며, 본문을 못 읽으면 매체가 써 둔 설명문(og:description)을 쓴다. 영문 기사는 설명문을 우선하고 요약문을 번역한다. 결과는 7일 캐시한다.
- 화면은 목록 위 8건을 자동 요약하고 나머지는 `요약 보기`를 누를 때 가져온다. 임의 주소 조회를 막으려고 Google 뉴스 링크만 받고, 사설 IP로 풀리는 호스트는 조회하지 않는다.
- 한계: Barron's, Investing.com 등 자동 조회를 막거나 유료인 매체는 요약하지 못한다(실측 23건 중 20건 성공). 문장을 새로 쓰는 요약이 아니라 원문 문장 발췌다. `beautifulsoup4`를 `requirements.txt`에 추가했다.
- 테스트는 `py -m unittest discover -s tests` 19건 통과. 실제 Redis로 띄운 서버에서 API와 화면을 확인했다.

### AI 요약 연결 (Gemini 무료 등급)

- `.env`에 `AI_API_KEY`, `AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `AI_MODEL=gemini-3.8-flash`를 넣어 AI 요약을 켰다. 기사 요약, 호재/악재 분류, `분석 실행` 해석이 모두 같은 키를 쓴다.
- `app/ai_client.py`의 `chat_completion`으로 AI 호출을 모았다. 모델이 429/5xx를 주거나 시간 안에 답하지 않으면 `AI_FALLBACK_MODELS`의 모델을 차례로 시도하고, 실패한 모델은 5분간 건너뛴다. 전부 실패하면 규칙 기반 결과로 대체한다.
- 2026-10-14 실측에서 무료 등급의 `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`는 503(high demand)과 시간 초과가 잦았고 `gemini-3.1-flash-lite`만 안정적이었다. 그래서 `AI_FALLBACK_MODELS=gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite`로 두었다.
- AI가 실패해 규칙 기반으로 대신한 요약은 10분만 캐시한다(처음에는 7일 캐시되어 AI가 살아나도 발췌 요약이 남는 문제가 있었다).
- `.env`는 `--reload`가 감시하지 않으므로 값을 바꾸면 서버를 재시작해야 한다.

### 시장 분석 탭 리뉴얼

- 예전 `시장 분석` 패널(랭킹 표본만 해석)을 걷어내고, 첫 화면으로 새 `시장 분석` 탭을 두었다. `app/briefing.py`와 `GET /api/v1/market-briefing?market=kr|us&refresh=`가 다음을 한 번에 종합한다.
  - 지수 흐름: 국장은 KOSPI·KOSDAQ, 미장은 토스에 미국 지수가 없어 SPY·QQQ ETF로 대신한다(1일·1주·1개월·3개월, 최근 3개월 흐름).
  - 분야 흐름: 공식 섹터 기준 강한/약한 분야 3개씩, 흐름 단계 분포, 반등 시도·상승 뒤 약세 분야.
  - 주목 분야 기사 논조: 강한 2개·약한 2개 분야의 최근 7일 기사(분야명 + 시총 상위 3종목, 미장은 미국 대표 종목만)와 논조-가격 일치/괴리.
  - 랭킹 표본 해석: 기존 `interpret_market` 결과와 상승·하락·거래대금 상위 종목. 표본임을 화면에 표시한다.
- 요약(제목·종합 해석·핵심 포인트)은 AI가 위 자료만 근거로 쓰고, AI가 실패하면 규칙 기반 문장으로 대체한다. 화면에 `AI 분석`/`규칙 기반 분석`이 표시된다. 결과는 5분 캐시하고, AI 실패나 랭킹 캐시 공백 때는 90초만 캐시한다.
- 화면의 지수·종목을 누르면 주가 흐름 탭, 분야를 누르면 분야 흐름·기사 탭으로 넘어간다. `/api/v1/market-analysis?ai=true`는 API로만 남아 있다.

### 여러 나라 기사 수집

- 미장 종목은 Google 뉴스 미국판에 더해 영국·인도·일본·독일·프랑스판도 조회한다(`app/sector_news.py`의 `EDITIONS`, `WORLD_EDITIONS`). 종목 기사는 전부, 미장 분야 기사는 시총 상위 2종목, 시장 분석 탭은 분야별 1종목에 적용한다. 영어권 다른 나라 판은 미국판과 겹치는 기사가 많고(엔비디아 기준 새 기사 5~15건), 일본·독일판이 현지어 기사로 20건 이상 새로 나왔다.
- 기사마다 `country`가 붙고 화면에 나라와 `수집 국가` 집계가 표시된다. 번역은 원문 언어 자동 감지로 바꿨고, 한국어·영어가 아닌 기사는 번역된 제목으로 호재/악재를 분류한다.
- 국장 종목은 영문 회사명이 `SamsungElec`처럼 약칭이라 해외 판 검색에 쓰기 어려워 한국 기사만 수집한다.
- 미장 영문 검색은 티커도 함께 쓰므로 같은 티커를 쓰는 다른 회사 기사가 섞일 수 있다(예: `ADM`으로 런던 상장 Admiral Group 기사). 사용자가 그대로 두기로 했다.

### 국장 종목의 해외 기사와 동일 티커 오인 방지

- 국장 종목도 해외 기사를 수집한다. 토스 `englishName`이 약칭(`SamsungElec`)이라 Yahoo 검색의 정식 영문명(`Samsung Electronics Co., Ltd.`)을 `TossApiClient.fetch_public_name`으로 얻고, `clean_company_name`으로 `Samsung Electronics`로 줄여 미국·영국·인도·일본·독일·프랑스판에서 찾는다. 종목 기사는 전부, 분야 기사는 시총 상위 2종목, 시장 분석 탭은 분야별 1종목에 적용한다.
- 관련성 판정을 `is_relevant`로 모았다. 회사명이 제목에 있으면 인정하고, 티커만 있는 제목은 미국판에서만 인정하며 `LSE:ADM`처럼 미국 밖 거래소가 붙은 티커는 제외한다. 사용자는 처음에 그대로 두자고 했다가 해결을 요청했다.
- 한계: 일본어·중국어로 회사명을 적은 기사(サムスン電子 등)는 영문명이 제목에 없어 잡히지 않는다. 거래소 표기 없이 티커만 쓴 미국판 기사 중 다른 회사 기사는 여전히 섞일 수 있다.
- 테스트 23건 통과(`RelevanceTest` 추가).

### 시장 분석 통합(비교) 보기

- `GET /api/v1/market-briefing?market=all`과 화면의 `통합(비교)` 선택을 추가했다. 두 시장의 종목·금액을 합치지 않고, 국장·미장 분석을 나란히 놓은 뒤 같은 토스 섹터의 분야끼리 1일 등락률을 비교한다(`app/briefing.py`의 `compare_sector_flows`: 동반 상승, 동반 하락, 국장만 상승, 미장만 상승).
- 비교 해석은 AI가 쓰고 실패하면 규칙 기반 문장으로 대체한다. 섹터 API 한도 때문에 두 시장을 차례로 만들어 첫 조회는 30초쯤 걸린다.
- 미장은 한국 시간 직전 밤 거래분이라 국장과 시차가 있다는 점을 화면에 표시한다. 테스트 24건 통과.

### 주식 병합·분할 알림 (기간별 주가 흐름)

- 기간별 주가 흐름은 토스 캔들의 기본값인 수정주가(`adjusted=true`)를 쓰므로 병합으로 인한 가격 점프가 원래 등락률에 들어가지 않는다. 다만 화면에 그 사실이 보이지 않아 알림을 추가했다.
- `app/history.py`의 `detect_price_adjustments`가 수정주가와 원주가(`adjusted=false`)의 비율이 바뀌는 날을 찾아 병합·분할·기타 권리 조정으로 구분한다. 화면에는 날짜, 비율, 원주가 기준 등락과 수정주가 기준 등락을 함께 보여 주고 차트에 주황 점선으로 표시한다.
- 실측: POAS 15:1(2026-10-12, 원주가 +1349.28% / 수정주가 -3.38%), ZBAO 50:1, DCX 160:1, OPNW 10:1. 미장 랭킹 150종목 중 최근 200거래일에 비율 변화가 71건 있었다.
- 토스 매수 유의사항(`/api/v1/stocks/{symbol}/warnings`: 정리매매, 투자경고·위험, 단기과열, VI)도 함께 표시한다. 확인 시점에 활성 유의사항이 있는 종목을 찾지 못해 실제 데이터로는 표시를 확인하지 못했다.
- 예정된 병합 공시를 미리 알려 주는 데이터는 토스 API에 없다. 테스트 27건 통과.

## 2026-10-23 작업 기록

### 중간 보고 준비 실행

- 서버와 Redis를 실행해 실제 데이터로 전 기능을 점검했다. 코드는 10-14 커밋(`7561091`) 이후 바뀌지 않았다(그 사이 커밋은 문서·PDF 추가뿐).
- Redis 컨테이너가 떠 있어도 호스트 6379 포트가 연결되지 않는 문제가 있었다(`docker ps`의 Ports가 `6379/tcp`로만 표시). `docker compose up -d --force-recreate redis`로 다시 만들어 `0.0.0.0:6379->6379/tcp`로 연결됐다. 같은 증상이 나오면 이 명령을 먼저 쓴다.
- 실측(2026-10-23): 랭킹 6종 × 50개 수집·종목명 300/300, 분야 흐름 국장 38·미장 39개, 기사 종목당 100건(삼성전자 한국 57·미국 20·일본 12 등), 외국어 기사 번역 전부 성공, 기사 본문 요약 12건 중 9건 성공, 엔비디아 1:10 분할(2024-06-10)과 POAS 15:1 병합(2026-10-12) 감지, 테스트 27건 통과.
- 보고서용 화면 캡처 8장을 `report_screenshots/`에 저장했다(커밋하지 않음). 사용자는 직접 캡처하기로 했다.
- 점검 후 서버와 Redis 컨테이너를 종료했다.

### 새로 확인한 한계

- 첫 조회가 느리다: 기사 100건 수집 + AI 분류로 종목 16~66초, 시장 분석 52~70초. 이후는 캐시.
- Gemini 무료 등급이 이날도 불안정해 미장 시장 분석과 일부 기사 분류가 규칙 기반으로 대체됐다.
- 기사 관련성 오류: 한글 종목명이 영문인 경우(`RTX`) 엔비디아 그래픽카드 "RTX" 기사가 섞이고, `General Electric` 검색에 "General Electric Credit Union" 기사가 섞였다.
- 원유·가스에너지처럼 대표 종목이 같은 분야는 시장 분석 탭의 기사 논조가 같게 나온다(버그 아님).

## 현재 상태와 다음 재개 지점

- 기능: 랭킹 수집(KR/US), 공식 섹터 분야 흐름, 기간별 주가 흐름(최대 5년, 병합·분할 알림, 매수 유의사항), 한국+해외 6개국 기사 수집·번역·호재/악재 분류·본문 요약, AI 시장 분석(국장/미장/통합 비교).
- 주요 모듈: `app/briefing.py`(시장 분석), `app/sectors.py`(분야 흐름·리포트), `app/history.py`(주가 이력·병합 감지), `app/sector_news.py`(기사 수집·관련성), `app/news_classifier.py`(분류), `app/article_summary.py`(본문 요약), `app/translator.py`(번역), `app/ai_client.py`(AI 호출·예비 모델), `app/api/insight_routes.py`(새 API).
- 다음 후보: 관심 종목·분야 저장과 알림, 뉴스 출처를 정식 API(네이버 검색 API 등)로 교체, 한글 종목명이 영문인 종목의 기사 오인 보완, 첫 조회 속도 개선(미리 수집), 시장 분석 로직 테스트 보강.
