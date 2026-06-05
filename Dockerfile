FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt .

RUN apk add --no-cache ffmpeg libsodium \
    && apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev libsodium-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY . .

ENV FFMPEG_PATH=ffmpeg

CMD ["python3", "main.py"]
