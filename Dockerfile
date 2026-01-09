# Use official Python runtime
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Expose port (Render/Fly usually use 8080 or 10000, Flask default is 5000)
# We will use an environment variable for port or default to 5000
CMD ["python", "server.py"]
