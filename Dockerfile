# Keep runtime Python compatible with pinned deps in requirements.txt.
# (Project originally targets python3.9+; 3.10 is a safe default for 2022-era libs.)
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps (sqlite already available via stdlib; tzdata for ZoneInfo).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Fail fast if code has syntax errors.
RUN python -m compileall -q /app

# Persist state + candle cache under /data (mounted PVC)
WORKDIR /data

CMD ["python", "/app/app/main.py"]

