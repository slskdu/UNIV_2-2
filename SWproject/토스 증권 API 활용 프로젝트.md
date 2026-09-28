# 토스 증권 API 활용 프로젝트

## 1. 프로젝트 개요

토스증권 Open API에서 국내 주식 랭킹 데이터를 주기적으로 수집하고, Redis에 캐싱한 뒤 FastAPI API로 제공하는 백엔드 프로젝트입니다.

현재 구성은 다음 흐름으로 동작합니다.

```text
토스증권 Open API
        |
        v
OAuth 2.0 인증 및 access token 발급
        |
        v
랭킹 데이터 수집기
        |
        v
Redis 캐시 저장
        |
        v
FastAPI /api/v1/market-trend
        |
        v
프론트엔드 또는 Chrome Extension
```

## 2. FastAPI 세팅

### 주요 구성

- FastAPI 애플리케이션 구성
- Uvicorn 개발 서버 사용
- CORS 설정
- 애플리케이션 시작/종료 시 리소스 관리
- 20초 주기 백그라운드 랭킹 수집
- Swagger API 문서 제공

### 주요 실행 명령

```powershell
cd C:\UNIV_2-2\SWproject
pip install -r requirements.txt
docker compose up -d redis
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 주요 주소

```text
헬스 체크
http://127.0.0.1:8000/health

랭킹 데이터 API
http://127.0.0.1:8000/api/v1/market-trend

랭킹 표 화면
http://127.0.0.1:8000/market-trend

FastAPI 테스트 문서
http://127.0.0.1:8000/docs
```

`/docs`는 사용자용 주식 화면이 아니라 백엔드 API를 확인하고 직접 테스트하는 Swagger UI입니다.
`/market-trend`는 Redis에 저장된 상승률, 하락률, 거래대금 데이터를 표 형태로 보여주는 웹 화면입니다. 화면은 20초마다 자동으로 새로고침됩니다.
화면 상단의 `통합`, `국장`, `미장` 버튼으로 시장을 선택할 수 있습니다. 통합 화면은 KR과 US 행을 함께 표시합니다.
투자 유의 종목은 `excludeInvestmentCaution=false`로 설정하여 제외하지 않습니다. 표의 분야는 종목 마스터가 제공하는 상품 유형(`STOCK`, `ETF` 등)과 거래소(`KOSPI`, `NASDAQ` 등)를 표시합니다. 공식 종목 기본 정보 API에는 산업 섹터(반도체·자동차 등)가 제공되지 않습니다.

## 3. 토스증권 Open API 연동

공식 문서:

```text
https://developers.tossinvest.com/docs
```

사용 중인 공식 REST 주소:

```text
https://openapi.tossinvest.com
```

### 인증

OAuth 2.0 Client Credentials Grant 방식을 사용합니다.

```text
POST /oauth2/token
Content-Type: application/x-www-form-urlencoded
```

전송 값:

```text
grant_type=client_credentials
client_id=발급받은 client ID
client_secret=발급받은 client secret
```

발급받은 access token은 다음처럼 사용합니다.

```text
Authorization: Bearer {access_token}
```

### 랭킹 API

```text
GET /api/v1/rankings
```

현재 사용하는 랭킹 종류:

- `TOP_GAINERS`: 상승률 상위
- `TOP_LOSERS`: 하락률 상위
- `MARKET_TRADING_AMOUNT`: 시장 거래대금 상위

주요 query parameter:

```text
type=TOP_GAINERS
marketCountry=KR
duration=1d
excludeInvestmentCaution=false
count=50
```

`count`는 공식 API 제한에 맞춰 1~100 범위로 보정합니다.

시장 구분:

- `marketCountry=KR`: 국장(KRX)
- `marketCountry=US`: 미장(NYSE, NASDAQ 등)
- 화면의 `통합`: KR과 US를 각각 조회한 뒤 합쳐서 표시

## 4. Redis 캐싱

Redis는 토스 API에서 수집한 랭킹 데이터를 임시 저장합니다.

### 캐시 키

```text
market:ranking:kr:rising
market:ranking:kr:falling
market:ranking:kr:trading_value
market:ranking:us:rising
market:ranking:us:falling
market:ranking:us:trading_value
```

### 캐시 동작

- 기본 TTL: 30초
- 국장·미장 및 랭킹 종류별 개별 저장
- 일부 랭킹 요청이 실패해도 성공한 종류의 기존 캐시는 유지
- FastAPI API 요청 시 토스 API를 직접 호출하지 않고 Redis 데이터 반환
- Redis에 데이터가 하나도 없으면 HTTP 503 반환

## 5. 오류 해결 내역

### 5.1 서버 접속 거부

증상:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

두 주소 모두 연결 거부가 발생했습니다.

원인:

- Uvicorn이 설정 로딩 단계에서 종료됨
- `.env`에 이전 버전의 `TOSS_ACCESS_TOKEN` 설정만 존재
- 현재 코드가 요구하는 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`이 누락

해결:

```env
TOSS_CLIENT_ID=실제 client id
TOSS_CLIENT_SECRET=실제 client secret
```

설정을 추가한 뒤 FastAPI import와 서버 기동을 확인했습니다.

### 5.2 토큰 엔드포인트 403 오류

증상:

```text
POST https://openapi.tossinvest.com/v1/oauth2/token
403 edge-blocked
```

원인:

`.env`의 API 기본 주소에 잘못된 `/v1`이 포함되어 있었습니다.

잘못된 설정:

```env
TOSS_API_BASE_URL=https://openapi.tossinvest.com/v1
```

올바른 설정:

```env
TOSS_API_BASE_URL=https://openapi.tossinvest.com
```

해결 후 다음 요청이 정상 처리되었습니다.

```text
POST https://openapi.tossinvest.com/oauth2/token -> 200
GET https://openapi.tossinvest.com/api/v1/rankings -> 200
```

### 5.3 랭킹 파라미터 오류 예방

초기 코드에는 `limit` 파라미터가 사용되었지만, 공식 랭킹 문서의 파라미터는 `count`입니다.

현재 코드에서 다음처럼 수정했습니다.

```text
ranking_count_param=count
ranking_count=50
```

### 5.4 최종 동작 확인

실제 서버에서 다음 결과를 확인했습니다.

```text
토큰 발급: 성공
상승 랭킹: 50개 수집
하락 랭킹: 50개 수집
거래대금 랭킹: 50개 수집
/api/v1/market-trend: HTTP 200
source: redis
```

## 6. 현재 파일 구조

```text
SWproject/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── auth.py
│   ├── redis_client.py
│   ├── toss_api.py
│   ├── collector.py
│   └── api/
│       ├── __init__.py
│       └── routes.py
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── docker-compose.yml
└── 토스 증권 API 활용 프로젝트.md
```

## 7. 다음 작업 목표

> 아래 7장의 초기 목표는 이후 구현으로 대부분 완료되었습니다. 최신 상태는 9장 최종 구현 상태를 기준으로 확인합니다.

## 8. 분야 분석 및 AI 요약

`/api/v1/market-analysis?market=all`은 랭킹 데이터를 분야별로 묶어 평균 등락률, 상승 종목 비율, 거래대금 비중, 강세 점수를 계산합니다.

웹 화면의 `현재 강세 분야`, `주의 분야` 패널은 이 분석 결과를 표시합니다. `AI 요약 생성` 버튼은 `AI_API_KEY`가 없으면 로컬 수치 요약을 사용하고, 키가 있으면 OpenAI 호환 Chat Completions API를 호출합니다.

공식 종목 기본 정보 API에는 산업 분야가 없으므로, 산업 분야를 사용하려면 `sector_map.json`에 직접 매핑을 추가합니다.

```json
{
        "005930": {
                "weights": {
                        "반도체/메모리": 0.7,
                        "스마트폰": 0.2,
                        "가전": 0.1
                },
                "source": "대표 사업 기준 예시",
                "as_of": "2026-09"
        },
        "000660": "반도체",
        "005380": "자동차",
        "009150": "전자부품/MLCC",
        "042700": "반도체 후공정 장비",
        "006400": "배터리/2차전지"
}
```

문자열 값은 100% 단일 분야이고, `weights` 객체는 복합 기업을 여러 분야에 배분합니다. 가중치 합이 1이 아니어도 분석 시 자동 보정됩니다. 매핑된 종목은 세부 분야로 표시되고, 매핑되지 않은 종목은 분석에서 `미분류`로 집계됩니다.

현재 기본값은 `WEIGHTED_ANALYSIS_ENABLED=false`라서 가중치 구조를 보존만 하고 대표 분야 하나로 분석합니다. 정확한 사업부문 자료를 입력한 뒤 `.env`에서 `WEIGHTED_ANALYSIS_ENABLED=true`로 바꾸면 가중 분석이 활성화됩니다.

가중 분석의 신뢰도는 매핑 출처와 기준일에 좌우됩니다. 사업보고서·공식 기업 자료·토스 분야 정보처럼 검증 가능한 자료로 가중치를 갱신하고 `source`, `as_of`를 함께 기록해야 합니다. 가중치는 실제 매출·영업이익·시가총액 비중과 다를 수 있으므로 분석 결과는 참고용이며 투자 판단을 대신하지 않습니다.

## 9. 최종 구현 상태

### 웹 화면

```text
http://127.0.0.1:9000/market-trend
```

- `통합`, `국장`, `미장` 시장 버튼
- 상승률·하락률·거래대금 탭
- 통합 탭은 KR·US 데이터를 합친 뒤 지표 기준으로 다시 정렬
- 순위, 종목명, 종목코드, 세부분야, 현재가, 등락률, 거래량, 거래대금 표시
- `rankedAt` 기준 시각 표시
- 강세 분야·주의 분야 분석 카드
- 분야별 상세 분석 표
- AI 요약 버튼
- 20초 자동 갱신

### API 목록

```text
GET /health
GET /api/v1/market-trend?market=all|kr|us
GET /api/v1/market-analysis?market=all|kr|us&ai=false
```

### 분석 지표

- 분야별 평균 등락률
- 상승·하락 종목 비율
- 거래대금 비중
- 분야 점수
- 강세·중립·주의 신호
- 복합 기업 가중 분석 플래그

### 현재 보류한 기능

- `WEIGHTED_ANALYSIS_ENABLED=false`로 가중 분석은 비활성화되어 있습니다.
- `sector_map.json`의 가중치 구조는 보존되어 있으며, 공식 사업부문 자료를 검토한 뒤 활성화해야 합니다.
- 매핑되지 않은 종목은 `미분류`로 처리됩니다.
- AI 키가 없으면 로컬 수치 기반 요약을 사용합니다.

### 실행 순서

```powershell
cd C:\UNIV_2-2\SWproject
docker compose up -d redis
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 9000
```

Redis 포트 `6379`가 이미 다른 Redis에서 사용 중이면 기존 Redis를 그대로 사용합니다. 서버 실행 후 새 수집 주기가 끝나면 웹 화면에서 데이터를 확인합니다.

선택적 AI 설정:

```dotenv
AI_API_KEY=발급받은_AI_API_KEY
AI_MODEL=gpt-4o-mini
AI_BASE_URL=https://api.openai.com/v1
```

AI 분석은 투자 조언이 아니라 현재 랭킹 수치를 요약하는 참고 정보입니다.

### 1단계: API 응답 정리

- 토스 API의 원본 응답 전체를 그대로 전달하지 않고 프론트엔드용 응답 형식으로 정리
- 종목 코드, 현재가, 기준가, 등락률, 거래량, 거래대금 필드 표준화
- `rankedAt`과 데이터 수집 시각 추가

### 2단계: 프론트엔드 연결

- `http://127.0.0.1:8000/api/v1/market-trend` 호출
- 상승, 하락, 거래대금 랭킹 화면 구성
- 로딩, 빈 데이터, API 오류 상태 처리
- CORS에 실제 Chrome Extension ID 또는 프론트엔드 주소만 허용

### 3단계: 안정성 개선

- 토스 API 429 응답에 대한 지수 백오프와 jitter 적용
- 수집 실패 시 마지막 정상 데이터와 마지막 성공 시각 표시
- Redis 연결 실패 시 명확한 상태 메시지 제공
- API 요청 및 수집 오류 로그에 request ID 기록

### 4단계: 테스트 추가

- `/health` API 테스트
- Redis 저장/조회 테스트
- 토큰 캐시 테스트
- 401 응답 시 토큰 재발급 테스트
- 429 응답 시 재시도 테스트
- 랭킹 수집 성공 및 일부 실패 테스트

### 5단계: 배포 준비

- 실제 운영용 환경변수 분리
- `.env` 및 API 키가 Git에 포함되지 않는지 확인
- Docker 기반 FastAPI 실행 환경 구성
- 단일 worker 운영 또는 수집기를 별도 scheduler로 분리
- 토스 Open API 허용 IP에 배포 서버 공인 IP 등록

## 8. 주의사항

- 실제 API 키는 `.env`에만 저장하고 GitHub에 올리지 않습니다.
- API 키가 노출되면 토스증권 Open API 콘솔에서 즉시 폐기하고 재발급합니다.
- 토스 API 허용 IP에 현재 서버의 공인 IP가 등록되어 있어야 합니다.
- 여러 Uvicorn worker를 실행하면 worker마다 수집기가 중복 실행될 수 있습니다.
- Redis가 실행 중이어야 FastAPI가 정상적으로 시작됩니다.
- `.env`를 수정한 뒤에는 Uvicorn을 재시작해야 새 설정이 반영됩니다.
