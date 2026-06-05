FROM python:3.12-slim

WORKDIR /app

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY cloud_app.py web_app.py auth_store.py config.py paths.py screener.py data_fetcher.py http_client.py utils.py start.sh ./
COPY templates/ templates/
COPY static/ static/

RUN chmod +x start.sh

ENV MOBILE_ONLY=1
ENV PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["./start.sh"]
