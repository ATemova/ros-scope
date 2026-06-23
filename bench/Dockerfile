FROM python:3.12-slim
WORKDIR /app
COPY bench/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ ./common/
COPY bench/ ./bench/
ENTRYPOINT ["python", "-m", "bench.loadgen"]