# 운영 서버용 이미지. 실행: docker compose -f docker-compose.prod.yml up -d --build (DEPLOY.md 참고)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# 코드와 분야 지도만 넣습니다(.env·보고서·로그는 .dockerignore로 제외, .env는 실행할 때 env_file로 넘김).
COPY app ./app
COPY sector_map.json us_sector_map.json ./

# root가 아닌 사용자로 실행
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=4)"

# worker는 하나만: 수집기·분야 흐름 루프와 사용자별 토스 연결이 프로세스 안에 있어, 여러 개면 중복 수집·토큰 충돌이 납니다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
