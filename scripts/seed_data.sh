#!/bin/bash
# developlocal: run bootstrap.sh's "Seeding Initial Data" step on its own.
#
# Use it when bootstrap.sh's step 13 fails, e.g. on a corporate network that
# intercepts TLS (the Cloud SQL Proxy then fails with "x509: certificate signed
# by unknown authority"). Run it from Google Cloud Shell instead:
#
#   git clone -b developlocal https://github.com/<you>/gcc-creative-studio.git
#   cd gcc-creative-studio && bash scripts/seed_data.sh <project-id> [env-name]
#
# It reuses the functions in bootstrap.sh (proxy, DB secrets, seed_data), so the
# behaviour is identical to the installer's step. Safe to re-run.
set -uo pipefail
GCP_PROJECT_ID="${1:?usage: scripts/seed_data.sh <project-id> [env-name]}"
ENV_NAME="${2:-dev-infra}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v uv > /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
gcloud config set project "$GCP_PROJECT_ID" > /dev/null
# bootstrap.sh looks here for Terraform outputs; when absent it falls back to gcloud
mkdir -p "$REPO_ROOT/infra/environments/$ENV_NAME"

# Load bootstrap.sh's functions without running its main flow. Sourcing resets
# its globals (REPO_ROOT="", STATE_FILE="", ...), so set ours again afterwards.
_ROOT="$REPO_ROOT"; _PROJECT="$GCP_PROJECT_ID"; _ENV="$ENV_NAME"
source <(sed '$d' "$_ROOT/bootstrap.sh")
REPO_ROOT="$_ROOT"; GCP_PROJECT_ID="$_PROJECT"; ENV_NAME="$_ENV"
STATE_FILE=""   # nothing to resume here
[ -f "$REPO_ROOT/backend/pyproject.toml" ] || { echo "❌ Not a GCC checkout: $REPO_ROOT"; exit 1; }
cd "$REPO_ROOT"

# seed_data calls `fail` (exit) and sets its own EXIT trap, so run it in a subshell
if ( seed_data ); then
    echo "✅ Seed data loaded into project ${GCP_PROJECT_ID}."
else
    rc=$?
    echo "❌ Seeding failed (exit ${rc})."
    if [ -f "$REPO_ROOT/cloud-sql-proxy.log" ]; then
        echo "--- last lines of cloud-sql-proxy.log ---"; tail -n 15 "$REPO_ROOT/cloud-sql-proxy.log"
    fi
    exit "$rc"
fi
