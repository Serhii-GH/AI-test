FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY app ./app

CMD ["python", "-m", "app.main"]
