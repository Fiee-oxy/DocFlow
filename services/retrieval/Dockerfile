FROM dovideo-docling:local
WORKDIR /retrieval
ENV OMP_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false HF_HOME=/models/huggingface
RUN pip install --no-cache-dir pymilvus==2.6.4
COPY app.py retrieval_router.py chunk_strategies.py query_expansion.py synonyms.json document_structure.py ./
COPY config/retrieval-routing-rules.yml ./config/retrieval-routing-rules.yml
EXPOSE 5002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5002", "--workers", "1", "--limit-concurrency", "4"]
