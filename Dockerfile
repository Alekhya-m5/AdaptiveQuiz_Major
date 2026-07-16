FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/instance

EXPOSE 5000

# Gunicorn for production-style serving. run:app comes from run.py's `app` object.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "run:app"]
