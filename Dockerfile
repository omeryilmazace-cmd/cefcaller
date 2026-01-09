# Use official Python runtime
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Make start script executable
COPY start.sh .
RUN chmod +x start.sh

# Run the start script
CMD ["./start.sh"]
