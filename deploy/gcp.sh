#!/usr/bin/env bash
# Build, push and deploy Go Scan to Cloud Run (us-central1).
#
#   deploy/gcp.sh            build both images with a new tag and deploy both services
#   deploy/gcp.sh app        only the app
#   deploy/gcp.sh katago     only KataGo
#
# One-time setup already done (see deploy/README.md): Artifact Registry repo "go-scan", bucket
# <project>-go-scan, service accounts go-scan-run / go-katago-run, IAM bindings, IAP access.
# Secrets are cloud-bot's: cloud-bot-{gemini-api-key,qdrant-api-key,valkey-url,valkey-token}.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=$(gcloud config get-value project 2>/dev/null)
REGION=us-central1
REPO=$REGION-docker.pkg.dev/$PROJECT/go-scan
TAG=$(date +%Y%m%d-%H%M)
KATAGO_URL=https://go-katago-$(gcloud projects describe "$PROJECT" --format='value(projectNumber)').$REGION.run.app
# Non-secret Qdrant URL from cloud-bot's .env (never printed)
QDRANT_URL=$(grep -E '^QDRANT_URL=' ../cloud-bot/.env | cut -d= -f2- | tr -d "\"'")
what=${1:-all}

if [[ $what == all || $what == katago ]]; then
  docker build -t "$REPO/katago-cpu:$TAG" -f katago/Dockerfile.cpu katago
  docker push -q "$REPO/katago-cpu:$TAG"
  gcloud run deploy go-katago --project="$PROJECT" --region=$REGION --image="$REPO/katago-cpu:$TAG" \
    --service-account="go-katago-run@$PROJECT.iam.gserviceaccount.com" --no-allow-unauthenticated \
    --cpu=2 --memory=2Gi --min-instances=0 --max-instances=1 --concurrency=4 --timeout=120 --cpu-boost --port=8080
fi

if [[ $what == all || $what == app ]]; then
  docker build -t "$REPO/app:$TAG" .
  docker push -q "$REPO/app:$TAG"
  gcloud beta run deploy go-scan --project="$PROJECT" --region=$REGION --image="$REPO/app:$TAG" \
    --service-account="go-scan-run@$PROJECT.iam.gserviceaccount.com" --no-allow-unauthenticated --iap \
    --cpu=2 --memory=4Gi --min-instances=0 --max-instances=1 --concurrency=4 --timeout=180 --cpu-boost \
    --port=8000 --execution-environment=gen2 \
    --add-volume=name=data,type=cloud-storage,bucket="$PROJECT-go-scan" --add-volume-mount=volume=data,mount-path=/data \
    --set-env-vars="DATA_DIR=/data/scans,BOOK_PAGES_DIR=/data/books/pages,KATAGO_URL=$KATAGO_URL,KATAGO_AUTH=gcp,QDRANT_URL=$QDRANT_URL" \
    --set-secrets="GEMINI_API_KEY=cloud-bot-gemini-api-key:latest,QDRANT_API_KEY=cloud-bot-qdrant-api-key:latest,VALKEY_URL=cloud-bot-valkey-url:latest,VALKEY_TOKEN=cloud-bot-valkey-token:latest" \
    | grep -v QDRANT_URL
fi
