#!/usr/bin/env bash
# Build, push and deploy Go Scan to Cloud Run (us-central1).
#
#   deploy/gcp.sh            build both images with a new tag and deploy both services
#   deploy/gcp.sh app        only the app
#   deploy/gcp.sh katago     only KataGo (Modal GPU when the go-scan-modal-token secret exists, else Cloud Run CPU)
#   deploy/gcp.sh cleanup    only delete old revisions and images (also done after every deploy)
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
# Admin accounts (no usage limits): deploy/admins.txt, one e-mail per line, not in git
admins=$(grep -v '^\s*#' deploy/admins.txt 2>/dev/null | tr -d ' \r' | grep . | paste -sd, || true)
has_secret() { gcloud secrets describe "$1" --project="$PROJECT" >/dev/null 2>&1; }

# KataGo on a Modal L4 GPU (katago/modal_katago.py) once its proxy token is in Secret Manager
MODAL_KATAGO_URL=https://vuchauduytung--go-katago-katago-serve.modal.run
katago_env="KATAGO_URL=$KATAGO_URL|KATAGO_AUTH=gcp"
secrets="GEMINI_API_KEY=cloud-bot-gemini-api-key:latest,QDRANT_API_KEY=cloud-bot-qdrant-api-key:latest,VALKEY_URL=cloud-bot-valkey-url:latest,VALKEY_TOKEN=cloud-bot-valkey-token:latest"
if has_secret go-scan-modal-token; then
  katago_env="KATAGO_URL=$MODAL_KATAGO_URL|KATAGO_AUTH=modal"
  secrets+=",KATAGO_MODAL_TOKEN=go-scan-modal-token:latest"
fi
# Optional fallback LLM providers (rag/llm.py): used when their secret exists
for pair in GROQ_API_KEY:go-scan-groq-api-key OPENROUTER_API_KEY:go-scan-openrouter-api-key \
            MISTRAL_API_KEY:go-scan-mistral-api-key GITHUB_MODELS_TOKEN:go-scan-github-models-token; do
  has_secret "${pair#*:}" && secrets+=",${pair%%:*}=${pair#*:}:latest"
done

# Keep only the revisions serving traffic and their images: Artifact Registry is free up to 0.5 GB per billing
# account and every deploy pushes ~1.2 GB, so older images would be billed.
cleanup() {   # service, image name in $REPO
  local service=$1 image=$REPO/$2 keep digests r d
  keep=$(gcloud run services describe "$service" --project="$PROJECT" --region=$REGION \
    --format='value(status.traffic.revisionName)' | tr ';' ' ')
  [[ -n $keep ]] || { echo "cleanup $service: no revision serving traffic, nothing deleted"; return; }
  digests=$(for r in $keep; do gcloud run revisions describe "$r" --project="$PROJECT" --region=$REGION \
    --format='value(status.imageDigest)' | sed 's/.*@//'; done)
  for r in $(gcloud run revisions list --service="$service" --project="$PROJECT" --region=$REGION --format='value(metadata.name)'); do
    [[ " $keep " == *" $r "* ]] || gcloud run revisions delete "$r" --project="$PROJECT" --region=$REGION --quiet
  done
  for d in $(gcloud artifacts docker images list "$image" --format='value(version)'); do
    grep -qx "$d" <<<"$digests" || gcloud artifacts docker images delete "$image@$d" --delete-tags --quiet
  done
}

if [[ ($what == all || $what == katago) && $katago_env == *modal* ]]; then
  modal deploy katago/modal_katago.py
elif [[ $what == all || $what == katago ]]; then
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
    --set-env-vars="^|^DATA_DIR=/data/scans|SESSIONS_DIR=/data/sessions|BOOK_PAGES_DIR=/data/books/pages|$katago_env|TRUST_IAP_HEADER=1|ADMIN_EMAILS=$admins|QDRANT_URL=$QDRANT_URL" \
    --set-secrets="$secrets" \
    | { grep -v QDRANT_URL || true; }
fi

if [[ ($what == all || $what == katago || $what == cleanup) && $katago_env != *modal* ]]; then cleanup go-katago katago-cpu; fi
if [[ $what == all || $what == app || $what == cleanup ]]; then cleanup go-scan app; fi
