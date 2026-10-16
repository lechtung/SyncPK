FROM python:3.12-slim

# Create unprivileged user for security
RUN useradd -m -u 1000 syncpkuser

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    SYNC_IS_DOCKER=true

# Set working directory
WORKDIR /app

# Install necessary system packages (tzdata for timezones, gosu for stepping down from root)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gosu \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies
COPY server/requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application files to workdir
COPY server/ ./

# We do not copy update.sh or check_update.sh because they are useless in Docker.

ARG VERSION=0.0.0
RUN echo "${VERSION#v}" > .ver

# Create cache directories and set ownership of /app/data
RUN mkdir -p /app/data/cache/posters /app/data/cache/fanarts && chown -R syncpkuser:syncpkuser /app/data

# Copy entrypoint script and make it executable
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Configure Healthcheck using the time API endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/api/time || exit 1

EXPOSE 8000

# Start the application via entrypoint
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]