FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt
COPY backend ./backend
COPY simulator ./simulator

RUN addgroup --gid 10001 bikeflow \
    && adduser --uid 10001 --gid 10001 --disabled-password --gecos "" --no-create-home bikeflow \
    && chown -R bikeflow:bikeflow /app
USER 10001:10001

ENTRYPOINT ["python", "-m", "simulator"]
