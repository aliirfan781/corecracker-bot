FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install standard Solidity compilers for Slither
RUN solc-select install 0.8.20 && solc-select use 0.8.20
RUN solc-select install 0.4.24

# Copy your bot code
COPY bot.py .

# Run the bot
CMD ["python", "bot.py"]