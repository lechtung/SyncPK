FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install necessary system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies
COPY requirements.txt .

# If requirements.txt doesn't have the necessary libraries, create a fallback
RUN if [ ! -s requirements.txt ] || ! grep -q "fastapi" requirements.txt; then \
        echo "fastapi\nuvicorn\nrequests\npython-dotenv\npython-multipart\nhttpx" > requirements.txt; \
    fi

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py .
COPY static/ static/

# Expose port 8000
EXPOSE 8000

# Start the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
