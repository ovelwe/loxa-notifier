# Образ для любого хостинга с Docker (Oracle Cloud VM, Fly.io, Railway, Koyeb…)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Папка для state.json — подключайте как volume, чтобы состояние не терялось
VOLUME ["/app/data"]
ENV STATE_FILE=/app/data/state.json

# По умолчанию — разовая проверка (удобно для cron / docker run).
# Для режима «постоянно в фоне» используйте docker-compose (интервал в SECONDS).
CMD ["python", "bot.py", "check"]
