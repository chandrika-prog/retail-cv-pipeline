FROM python:3.10-slim

WORKDIR /workspace

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY pipeline/ ./pipeline/
COPY docs/ ./docs/
COPY *.py ./
COPY README.md ./

ENV PYTHONPATH=/workspace/app:/workspace/pipeline

EXPOSE 8000

CMD ["uvicorn", "main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8000"]
