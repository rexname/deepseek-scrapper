FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PostgreSQL and Playwright
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# Gunakan CMD, bukan RUN untuk menjalankan uvicorn
CMD ["uvicorn", "main:api_app", "--host", "0.0.0.0", "--port", "8000"]