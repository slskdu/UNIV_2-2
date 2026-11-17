# 실서버 배포 (클라우드 리눅스 VM · IP로 시험 접속)

이 문서는 Ubuntu VM 한 대에 앱과 Redis를 Docker로 올려 `http://<서버 IP>/`로 접속하는 **시험 배포** 순서입니다.
HTTPS가 없으므로 로그인 비밀번호와 토스 키가 암호화되지 않은 채 오갑니다. 그래서 아래 "시험 배포 안전 수칙"을 지키고, 공개 운영 전에는 맨 아래 "HTTPS 붙이기"를 하세요.

## 준비물

| 항목 | 내용 |
|---|---|
| VM | Ubuntu 22.04/24.04, 1 vCPU · RAM 1GB 이상(2GB 권장), 디스크 20GB |
| 고정 공인 IP | AWS Lightsail "Static IP", Oracle Cloud "Reserved Public IP" 등. 토스 허용 IP에 넣을 값이라 바뀌면 안 됩니다 |
| 방화벽 | 22(SSH)와 80(HTTP)만 열기. **6379(Redis)·9000은 열지 않음** |
| 토스 Open API 키 | 서버 시세용 키. 허용 IP에 서버 공인 IP 추가 |

## 시험 배포 안전 수칙

1. **80번 포트는 내 IP에서만 열기**: 클라우드 방화벽(보안 그룹)에서 80번의 출발지를 내 공인 IP로 제한합니다. 다른 사람에게 보여 줄 때만 잠깐 넓힙니다.
2. **`TRADING_ENABLED=false`**: 서버에서는 실제 주문을 끕니다(HTTP 위에서 실제 주문은 하지 않음).
3. **`SIGNUP_INVITE_CODE` 설정**: 초대 코드를 아는 사람만 가입하게 합니다.
4. **토스 키 등록은 HTTPS 이후에**: HTTP 시험 중에는 내 계좌 탭에 토스 키를 등록하지 않는 것을 권장합니다.
5. **같은 토스 키를 두 곳에서 동시에 쓰지 않기**: 토스는 키 하나에 유효한 토큰을 하나만 둡니다. 서버와 내 PC가 같은 키로 동시에 돌면 서로 토큰을 끊습니다. 서버를 켜 둘 동안 PC 서버는 끄거나, 서버용 키를 따로 발급하세요.

## 1. VM에 Docker 설치

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # 다시 로그인하면 sudo 없이 docker 사용
```

## 2. 코드 올리기

GitHub 저장소에서 받기(커밋·푸시된 코드만 올라갑니다):

```bash
git clone https://github.com/slskdu/UNIV_2-2.git
cd UNIV_2-2/SWproject
```

비공개 저장소이거나 커밋 전 코드를 올릴 때는 PC에서 복사합니다(`.env`는 빼고):

```bash
# PC(Git Bash)에서
scp -r app requirements.txt sector_map.json us_sector_map.json Dockerfile .dockerignore docker-compose.prod.yml .env.example <사용자>@<서버 IP>:~/SWproject/
```

## 3. 서버용 `.env` 만들기

`.env`는 서버에서 직접 만들고, 채팅·Git·메신저로 보내지 않습니다.

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

PC의 `.env`와 다르게 정할 값:

| 키 | 서버 값 |
|---|---|
| `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` | 서버 시세용 키(허용 IP에 서버 IP 추가) |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost,<서버 공인 IP>` (도메인이 생기면 추가) |
| `TRADING_ENABLED` | `false` |
| `APP_SECRET_KEY` | 새로 만든 48자 값: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` (한 번 정하면 바꾸지 않음) |
| `SIGNUP_INVITE_CODE` | 초대 코드 |
| `SESSION_COOKIE_SECURE` | HTTP 시험 중 `false`, HTTPS 후 `true` |
| `CORS_ORIGINS` | 확장 프로그램을 쓸 때 `chrome-extension://<확장 ID>` |
| `AI_API_KEY`, `AI2_API_KEY` | AI 분석을 쓸 때 |

`REDIS_URL`은 `docker-compose.prod.yml`이 `redis://redis:6379/0`으로 덮어쓰므로 그대로 둬도 됩니다.

## 4. 실행

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps        # app이 healthy인지
docker compose -f docker-compose.prod.yml logs -f app   # Ctrl+C로 빠져나옴
```

확인: 브라우저에서 `http://<서버 IP>/market-trend`. 랭킹이 비어 있고 로그에 `401`·`403`이 보이면 토스 허용 IP 또는 키를 확인하세요.

## 5. 업데이트·재시작·백업

```bash
git pull                                              # 또는 scp로 다시 복사
docker compose -f docker-compose.prod.yml up -d --build   # 새 코드로 다시 만들기(데이터 유지)
docker compose -f docker-compose.prod.yml restart app     # .env만 바꿨을 때
```

- 데이터(회원·가상 포트폴리오·추천 기록)는 `market-redis-data` 볼륨에 남습니다. `down`은 괜찮지만 **`down -v`는 데이터를 지웁니다.**
- 백업: `docker exec market-redis redis-cli SAVE && docker cp market-redis:/data/dump.rdb ./backup-$(date +%F).rdb` (회원 정보·암호화된 토스 키가 들어 있으니 안전한 곳에 보관)
- VM이 재부팅돼도 `restart: unless-stopped`라 자동으로 다시 켜집니다.

## 6. (선택) PC의 데이터 옮기기

PC의 회원·가상 포트폴리오를 그대로 쓰려면, **PC와 같은 `APP_SECRET_KEY`**를 서버 `.env`에 넣고(다르면 저장된 토스 키를 풀 수 없음) PC Redis의 dump.rdb를 서버 볼륨에 넣어야 합니다. 새로 시작하는 편이 간단하므로, 필요할 때 따로 진행합니다.

## HTTPS 붙이기(공개 운영 전)

1. 도메인 준비(구매 또는 DuckDNS 같은 무료 서브도메인) → 서버 IP로 연결
2. 방화벽에 443 열기, 앱 포트를 `127.0.0.1:9000:9000`으로 바꾸고 앞에 Caddy(자동 인증서)를 둠
3. `.env`: `ALLOWED_HOSTS`에 도메인 추가, `SESSION_COOKIE_SECURE=true`
4. 프록시 뒤에서는 접속자 IP가 프록시 IP로 보이므로, 남용 제한이 IP별로 동작하게 uvicorn에 `--proxy-headers --forwarded-allow-ips=<프록시 IP>`를 추가
