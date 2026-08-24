FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY image_mcp.py .

# Glama provides the container command via the "CMD arguments" field in the admin UI.
# Example: ["python3", "image_mcp.py"]
# Do not set ENTRYPOINT here so the UI command is executed exactly as provided.
