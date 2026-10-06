cd /d C:\UNIV_2-2\SWproject
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 9000# 프로젝트 대화 기록

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
