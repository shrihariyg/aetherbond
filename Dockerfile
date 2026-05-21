FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Install package in editable mode
RUN pip install -e .

# Expose default UDP port
EXPOSE 51820/udp

ENTRYPOINT ["python", "-m", "aetherbond.server.main"]
