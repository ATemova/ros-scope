FROM python:3.12-slim
WORKDIR /app
COPY sim/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ ./common/
COPY sim/ ./sim/
CMD ["python", "-m", "sim.publisher"]
