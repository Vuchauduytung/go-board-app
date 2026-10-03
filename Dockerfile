FROM python:3.10-slim
WORKDIR /app
# gr/utils.py imports PIL.ImageTk (needs Tk libs)
RUN apt-get update && apt-get install -y --no-install-recommends libtk8.6 tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# bake the embedding model into the image: no download on Cloud Run cold start
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"
COPY models/ models/
COPY gr/ gr/
COPY img2sgf/ img2sgf/
COPY static/ static/
COPY rag/*.py rag/
COPY app.py moku.py coach.py accounts.py kgcache.py sessions.py review.py ./
ENV DATA_DIR=/data TZ=Asia/Ho_Chi_Minh HF_HUB_OFFLINE=1
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
