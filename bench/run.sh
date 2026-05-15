#!/usr/bin/env bash
# bench/run.sh — Anvil P-02 submission benchmark script
#
# Required by the PRD pre-submission checklist.
# Runs ingest + reconstruction via the official harness and emits report.json.
#
# Usage:
#   bash bench/run.sh                          # standard L2 run (default seeds)
#   bash bench/run.sh --stress                 # L3 stress run (30 svcs, 14 days)
#   bash bench/run.sh --docker                 # run inside Docker container
#
# Requirements: Python 3.12+, pip install -r requirements.txt
# For --docker: Docker Desktop must be running

set -euo pipefail

ADAPTER="adapters.engine:Engine"
MODE="fast"
OUT="report.json"

# Parse flags
STRESS=false
DOCKER=false
for arg in "$@"; do
  case $arg in
    --stress) STRESS=true ;;
    --docker) DOCKER=true ;;
  esac
done

if [ "$DOCKER" = true ]; then
  echo "==> Building Docker image anvil-p02..."
  docker build -t anvil-p02 .
  echo "==> Running self_check inside container..."
  docker run --rm anvil-p02 python self_check.py
  echo "==> Running stress bench inside container..."
  docker run --rm anvil-p02 python run.py \
    --adapter "$ADAPTER" \
    --mode "$MODE" \
    --seeds 42 101 202 303 404 \
    --n-services 30 --days 14 \
    --out report_stress.json
  echo "Docker parity check complete."
  exit 0
fi

if [ "$STRESS" = true ]; then
  echo "==> Running L3 stress benchmark (30 services, 14 days)..."
  python run.py \
    --adapter "$ADAPTER" \
    --mode "$MODE" \
    --seeds 42 101 202 303 404 \
    --n-services 30 --days 14 \
    --out report_stress.json
  echo "Stress report written to report_stress.json"
else
  echo "==> Running standard L2 self-check..."
  python self_check.py
  echo ""
  echo "==> Running multi-seed benchmark (default scale)..."
  python run.py \
    --adapter "$ADAPTER" \
    --mode "$MODE" \
    --seeds 42 101 202 303 404 \
    --out "$OUT"
  echo "Report written to $OUT"
fi
