#!/usr/bin/env bash
# Download the model files and KataGo binaries that are not stored in git.
# MODELS_ONLY=1: only the board recognition models (the Oracle ARM VM builds KataGo from source).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models katago
get() { [[ -s $2 ]] && echo "have $2" || { echo "get  $2"; curl -fsSL -o "$2" "$1"; }; }

# Board recognition models
get https://huggingface.co/kaya-go/moku-v4/resolve/main/model.onnx models/moku-v4.onnx   # AGPL-3.0
get https://github.com/noword/image2sgf/releases/download/v0.07/stone.pth models/stone.pth
if [[ ! -s models/board.pth ]]; then                                       # only shipped inside the release zip
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/rel.zip" https://github.com/noword/image2sgf/releases/download/v0.07/img2sgf.v0.07.zip
  unzip -q -j "$tmp/rel.zip" board.pth -d models && rm -rf "$tmp"
fi

[[ ${MODELS_ONLY:-} == 1 ]] && { echo done; exit 0; }

# KataGo: CUDA build + b18 net (local GPU), Eigen AVX2 build + b15 net (Cloud Run CPU)
NETS=https://media.katagotraining.org/uploaded/networks/models/kata1
if [[ ! -s katago/katago ]]; then
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/k.zip" https://github.com/lightvector/KataGo/releases/download/v1.18.2/katago-v1.18.2-cuda12.8-cudnn9.8.0-linux-x64.zip
  unzip -q -o "$tmp/k.zip" katago README.txt -d katago && rm -rf "$tmp"
fi
if [[ ! -s katago/katago-eigen ]]; then
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/k.zip" https://github.com/lightvector/KataGo/releases/download/v1.16.4/katago-v1.16.4-eigenavx2-linux-x64.zip
  unzip -q -o "$tmp/k.zip" katago -d "$tmp" && mv "$tmp/katago" katago/katago-eigen && rm -rf "$tmp"
fi
get $NETS/kata1-b18c384nbt-s9996604416-d4316597426.bin.gz katago/b18.bin.gz
get $NETS/kata1-b15c192-s1672170752-d466197061.txt.gz katago/b15.txt.gz
echo done
