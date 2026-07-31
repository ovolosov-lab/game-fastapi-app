FROM python:3.11.5-slim

WORKDIR /app

# Устанавливаем системные зависимости + утилиту wget для скачивания модели
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    gcc \
    g++ \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Создаем папку под модель и скачиваем Qwen 2.5 напрямую с Hugging Face
RUN mkdir -p /app/data
RUN wget -O /app/data/qwen2.5-0.5b-instruct-q4_k_m.gguf \
    https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=100 -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips=*"]