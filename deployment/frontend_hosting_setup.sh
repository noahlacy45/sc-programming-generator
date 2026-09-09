# =============================================================================
# One-time bucket setup
# =============================================================================

# 1. Create the bucket (name must be globally unique across all of GCS)
gcloud storage buckets create gs://sc-programming-frontend --project=norse-coral-441421-r9 --location=us-east4

# 2. Configure it to serve index.html as the main page
gcloud storage buckets update gs://sc-programming-frontend --web-main-page-suffix=index.html

# 3. Make it publicly readable (this is a static site, no auth needed to view it)
gcloud storage buckets add-iam-policy-binding gs://sc-programming-frontend --member=allUsers --role=roles/storage.objectViewer

# =============================================================================
# Upload the frontend (run this now, and again any time frontend/ changes)
# =============================================================================
# Resolves relative to *this script's own location*, not wherever you happen
# to run it from — so it works whether you're sitting in deployment/, the
# repo root, or anywhere else.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../frontend"
gcloud storage cp --recursive * gs://sc-programming-frontend/

# =============================================================================
# Your site is now live at:
# https://storage.googleapis.com/sc-programming-frontend/index.html
# =============================================================================
