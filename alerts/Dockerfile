FROM python:3.12-slim
WORKDIR /app
COPY alerts/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ ./common/
COPY alerts/ ./alerts/
CMD ["python", "-m", "alerts.engine"]
