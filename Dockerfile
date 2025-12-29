FROM python:3.10-slim

WORKDIR /app

# Copy backend requirements
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
# Copy backend code
COPY backend backend
# Copy frontend build
COPY frontend/dist frontend/dist

# Set python path
ENV PYTHONPATH=/app

# Run Gunicorn
# Cloud Run injects PORT, default is 8080.
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --worker-class uvicorn.workers.UvicornWorker --forwarded-allow-ips="*" --log-level debug backend.main:app --timeout 120
