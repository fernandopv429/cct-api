FROM python:3.12-slim
WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY services ./services
COPY api ./api
EXPOSE 8090
CMD ["uvicorn", "api.cct_api:app", "--host", "0.0.0.0", "--port", "8090"]
