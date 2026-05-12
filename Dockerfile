FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vienna ./vienna

ARG GIT_COMMIT=unknown
ENV VIENNA_COMMIT=${GIT_COMMIT}

EXPOSE 8080

CMD ["uvicorn", "vienna.server:app", "--host", "0.0.0.0", "--port", "8080"]
