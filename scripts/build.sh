#!/bin/bash
# Build the React frontend for production, then start FastAPI to serve everything.
# Run from the project root: bash scripts/build.sh
#
# Make this script executable first:
#   chmod +x scripts/build.sh

set -e

echo "Building frontend..."
cd frontend && npm run build && cd ..

echo ""
echo "Frontend built to frontend/dist/"
echo ""
echo "Start the server with:"
echo "  python -m uvicorn src.delivery.api:app --host 0.0.0.0 --port 8000"
echo ""
echo "Then open http://localhost:8000 in your browser."
