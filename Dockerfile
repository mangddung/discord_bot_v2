FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt .

# ffmpeg 제거: Lavalink가 오디오 디코딩/인코딩을 담당하므로 봇 컨테이너에 불필요
RUN apk add --no-cache libsodium \
    && apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev libsodium-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY . .

CMD ["python3", "main.py"]
