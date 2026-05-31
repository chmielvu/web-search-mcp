FROM python:3.11-slim

# Install system dependencies needed for compiling llama-cpp-python
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download the model. Using unsloth's quantization.
RUN wget -O /model.gguf "https://huggingface.co/unsloth/functiongemma-270m-it-GGUF/resolve/main/functiongemma-270m-it-Q8_0.gguf"

# Install python dependencies
COPY requirements.txt .
# We use CMAKE_ARGS to ensure it builds correctly for CPU
RUN CMAKE_ARGS="-DGGML_CPU=ON" pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY server.py .

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
