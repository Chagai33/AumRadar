FROM python:3.9-slim

WORKDIR /app

# Copy backend requirements
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend backend

# Set python path
ENV PYTHONPATH=/app

# Run Gunicorn
# Cloud Run injects PORT, default is 8080.
# We bind to 0.0.0.0:$PORT
CMD exec gunicorn --bind :$PORT --workers 1 --worker-class uvicorn.workers.UvicornWorker backend.main:app --timeout 0
