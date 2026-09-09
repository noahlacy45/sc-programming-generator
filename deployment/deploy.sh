#!/bin/bash

# S&C Program Generator - Cloud Run Deployment Script
# Same pattern as the drill library / mobility webpage deploy.sh files.

set -e

PROJECT_ID="norse-coral-441421-r9"
REGION="us-east4"
SERVICE_NAME="sc-programming-api"
DB_CONNECTION_NAME="norse-coral-441421-r9:us-east4:replay-baseball-player-dev"
GCS_BUCKET_NAME="sc-programming-reports"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Starting S&C Program Generator API Deployment${NC}"
echo "========================================"

if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed${NC}"
    exit 1
fi

echo -e "${GREEN}Setting GCP project...${NC}"
gcloud config set project $PROJECT_ID

echo -e "${GREEN}Enabling required APIs...${NC}"
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable sqladmin.googleapis.com
gcloud services enable storage.googleapis.com

echo -e "${GREEN}Setting up GCS bucket for generated PDFs...${NC}"
if gcloud storage buckets describe "gs://${GCS_BUCKET_NAME}" &> /dev/null; then
    echo "Bucket already exists, skipping creation."
else
    gcloud storage buckets create "gs://${GCS_BUCKET_NAME}" --project=$PROJECT_ID --location=$REGION
fi
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET_NAME}" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

echo -e "${GREEN}Building and deploying to Cloud Run...${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../backend"

gcloud run deploy $SERVICE_NAME \
  --source . \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120 \
  --max-instances 10 \
  --set-env-vars "DB_PORT=3306,GCP_PROJECT_ID=${PROJECT_ID},GCS_BUCKET_NAME=${GCS_BUCKET_NAME}" \
  --add-cloudsql-instances $DB_CONNECTION_NAME

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $REGION --format 'value(status.url)')

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Successful!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "API URL: ${YELLOW}$SERVICE_URL${NC}"
echo ""
echo "Test the health endpoint:"
echo "   curl \$SERVICE_URL/health"
echo ""
