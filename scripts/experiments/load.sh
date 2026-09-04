#!/usr/bin/env bash
set -euo pipefail

export SIM_BIKES="${SIM_BIKES:-50}"
export SIM_EVENTS_PER_SECOND="${SIM_EVENTS_PER_SECOND:-10}"
export SIM_DURATION_SECONDS="${SIM_DURATION_SECONDS:-60}"
export SIM_SEED="${SIM_SEED:-42}"
export SIM_DUPLICATE_PROBABILITY="${SIM_DUPLICATE_PROBABILITY:-0.10}"
export SIM_ANOMALY_PROBABILITY="${SIM_ANOMALY_PROBABILITY:-0.05}"

docker compose --profile simulation run --rm simulator

