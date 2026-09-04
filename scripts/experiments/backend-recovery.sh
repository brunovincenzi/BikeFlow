#!/usr/bin/env bash
set -euo pipefail

readonly NAMESPACE="bikeflow"
pod="$(kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/name=backend -o jsonpath='{.items[0].metadata.name}')"

if [[ -z "$pod" || "$pod" != backend-* ]]; then
  echo "No BikeFlow backend Pod found; refusing to delete anything" >&2
  exit 1
fi

echo "Deleting $NAMESPACE/$pod"
kubectl -n "$NAMESPACE" delete pod "$pod" --wait=false
kubectl -n "$NAMESPACE" rollout status deployment/backend --timeout=120s
kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/name=backend -o wide

