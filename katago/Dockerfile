FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
# katago is an AppImage: extract it (no FUSE in containers)
COPY katago /tmp/katago
RUN cd /tmp && ./katago --appimage-extract >/dev/null && mv squashfs-root /opt/katago && rm /tmp/katago
COPY b18.bin.gz /opt/katago/model.bin.gz
COPY analysis.cfg katago_server.py /opt/katago/
EXPOSE 8080
CMD ["python3", "/opt/katago/katago_server.py"]
