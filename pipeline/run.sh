#!/usr/bin/env sh
# One command: hackathon dataset → events.jsonl
set -e
cd "$(dirname "$0")/.."
python -m pipeline.run --dataset dataset --output events.jsonl "$@"
