FROM python:3.12-slim

WORKDIR /app

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY cloud_app.py web_app.py auth_store.py config.py paths.py screener.py data_fetcher.py http_client.py utils.py ./
COPY templates/ templates/
COPY static/ static/

ENV MOBILE_ONLY=1
ENV PORT=10000

EXPOSE 10000

CMD gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 180 cloud_app:app
