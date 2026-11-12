FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim && rm -rf /var/lib/apt/lists/*
COPY app.py prescan.py adaptive_pipeline.py normalization.py pipeline.py fast_pdf.py docx_stream.py docx_rich.py docx_batch_boundary.py ocr_config.py boundary_stream.py continuity.py chunking_document.py viewer.html ./
EXPOSE 5001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5001", "--workers", "1", "--limit-concurrency", "8"]
