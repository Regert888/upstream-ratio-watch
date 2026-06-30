FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    TZ=Asia/Shanghai \
    APP_TIMEZONE=Asia/Shanghai

WORKDIR /app

COPY app.py /app/app.py
COPY static /app/static

RUN mkdir -p /app/data

EXPOSE 8000

VOLUME ["/app/data"]

CMD ["python", "app.py"]
