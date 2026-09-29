FROM python:3.12-slim

# Create unprivileged user for security
RUN useradd -m -u 1000 syncpkuser

# Set working directory
WORKDIR /app

# Install necessary system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies (from server directory)
COPY server/requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (from server directory)
COPY server/main.py .
COPY server/static/ static/

# Create data and cache directories, then set ownership
RUN mkdir -p /app/data/cache/posters /app/data/cache/fanarts && chown -R syncpkuser:syncpkuser /app

# Switch to non-root user
USER syncpkuser

# Configure Healthcheck using the time API endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/time || exit 1

EXPOSE 8000

# Start the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]