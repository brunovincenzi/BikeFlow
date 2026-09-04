#!/usr/bin/env bash
set -euo pipefail

readonly NAMESPACE="bikeflow"
readonly SELECTOR="app.kubernetes.io/name=mongodb"

pods_text="$(kubectl -n "$NAMESPACE" get pods -l "$SELECTOR" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}')"
read -r -a pods <<< "$pods_text"
if [[ "${#pods[@]}" -ne 3 ]]; then
  echo "Expected exactly 3 MongoDB Pods in namespace $NAMESPACE, found ${#pods[@]}" >&2
  exit 1
fi

primary=""
for pod in "${pods[@]}"; do
  is_primary="$(kubectl -n "$NAMESPACE" exec "$pod" -- mongosh --quiet --eval 'db.hello().isWritablePrimary' | tr -d '[:space:]')"
  if [[ "$is_primary" == "true" ]]; then
    primary="$pod"
    break
  fi
done
if [[ -z "$primary" || "$primary" != mongo-[0-2] ]]; then
  echo "Unable to identify a safe BikeFlow MongoDB primary target" >&2
  exit 1
fi

before_count="$(kubectl -n "$NAMESPACE" exec "$primary" -- mongosh --quiet --eval 'db.getSiblingDB("bikeflow").telemetry.countDocuments({})' | tr -d '[:space:]')"
echo "Primary before failure: $primary; documents: $before_count"
kubectl -n "$NAMESPACE" delete pod "$primary" --wait=false

deadline=$((SECONDS + 120))
new_primary=""
while (( SECONDS < deadline )); do
  for pod in "${pods[@]}"; do
    [[ "$pod" == "$primary" ]] && continue
    if kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
      is_primary="$(kubectl -n "$NAMESPACE" exec "$pod" -- mongosh --quiet --eval 'db.hello().isWritablePrimary' 2>/dev/null | tr -d '[:space:]' || true)"
      if [[ "$is_primary" == "true" ]]; then
        new_primary="$pod"
        break 2
      fi
    fi
  done
  sleep 2
done
if [[ -z "$new_primary" ]]; then
  echo "No different primary elected within 120 seconds" >&2
  exit 1
fi

after_count="$(kubectl -n "$NAMESPACE" exec "$new_primary" -- mongosh --quiet --eval 'db.getSiblingDB("bikeflow").telemetry.countDocuments({})' | tr -d '[:space:]')"
duplicate_groups="$(kubectl -n "$NAMESPACE" exec "$new_primary" -- mongosh --quiet --eval '
  const result = db.getSiblingDB("bikeflow").telemetry.aggregate([
    {$group: {_id: "$event_id", count: {$sum: 1}}},
    {$match: {count: {$gt: 1}}},
    {$count: "groups"}
  ]).toArray();
  print(result.length ? result[0].groups : 0);
' | tr -d '[:space:]')"

echo "New primary: $new_primary; documents: $after_count; duplicated event_id groups: $duplicate_groups"
if (( after_count < before_count )); then
  echo "Document count decreased after failover" >&2
  exit 1
fi
if [[ "$duplicate_groups" != "0" ]]; then
  echo "Duplicate event_id values detected" >&2
  exit 1
fi

kubectl -n "$NAMESPACE" wait --for=condition=Ready "pod/$primary" --timeout=120s
kubectl -n "$NAMESPACE" get pods -l "$SELECTOR" -o wide

