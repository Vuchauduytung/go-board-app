#!/usr/bin/env bash
# Point go-scan's IAP at a custom OAuth client so personal Google accounts (outside the
# project's organization) can sign in. The Google-managed client only admits internal users.
#
#   deploy/iap-oauth.sh [path/to/iap-oauth.yaml]     (default ~/iap-oauth.yaml)
#
# File format (keep it chmod 600, never commit it):
#   accessSettings:
#     oauthSettings:
#       clientId: <CLIENT_ID>
#       clientSecret: <CLIENT_SECRET>
set -euo pipefail

FILE=${1:-$HOME/iap-oauth.yaml}
PROJECT=$(gcloud config get-value project 2>/dev/null)
REGION=us-central1

[[ -f $FILE ]] || { echo "Missing $FILE"; exit 1; }
chmod 600 "$FILE"
CLIENT_ID=$(grep -E '^\s*clientId:' "$FILE" | sed -E 's/.*clientId:\s*//' | tr -d "\"' ")
grep -qE '^\s*clientSecret:\s*\S+' "$FILE" || { echo "clientSecret is missing in $FILE"; exit 1; }
[[ $CLIENT_ID == *.apps.googleusercontent.com ]] || { echo "clientId does not look like an OAuth client ID"; exit 1; }

gcloud iap settings set "$FILE" --project="$PROJECT" --resource-type=cloud-run \
  --region=$REGION --service=go-scan --format=none
echo "Applied OAuth client $CLIENT_ID (secret not printed)."
echo "Redirect URI that must be registered on that client:"
echo "  https://iap.googleapis.com/v1/oauth/clientIds/$CLIENT_ID:handleRedirect"

# Verify: IAP should now redirect anonymous visitors to sign-in with *our* client ID
LOGIN=$(curl -s -o /dev/null -w '%{redirect_url}' "https://go-scan-$(gcloud projects describe "$PROJECT" --format='value(projectNumber)').$REGION.run.app/")
if [[ $LOGIN == *"client_id=$CLIENT_ID"* ]]; then echo "OK: sign-in now uses the custom client."
else echo "Sign-in redirect does not use the new client yet (can take a minute); rerun to recheck."; fi
