#!/usr/bin/env bash
set -euo pipefail

readonly NAMESPACE="bikeflow"
backend_pod="$(kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/name=backend -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$backend_pod" || "$backend_pod" != backend-* ]]; then
  echo "No BikeFlow backend Pod found" >&2
  exit 1
fi

kubectl -n "$NAMESPACE" exec "$backend_pod" -- python -c '
import json
import urllib.request

with urllib.request.urlopen("http://backend:8000/api/v1/telemetry?limit=1", timeout=5) as response:
    payload = json.load(response)
assert response.status == 200
print("API available; sample events returned: {}".format(len(payload["items"])))
'

mongo_pod="$(kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/name=mongodb -o jsonpath='{.items[0].metadata.name}')"
kubectl -n "$NAMESPACE" exec "$mongo_pod" -- mongosh --quiet --eval '
const dbRef = db.getSiblingDB("bikeflow");
const duplicates = dbRef.telemetry.aggregate([
  {$group: {_id: "$event_id", count: {$sum: 1}}},
  {$match: {count: {$gt: 1}}},
  {$count: "groups"}
]).toArray();
printjson({documents: dbRef.telemetry.countDocuments({}), duplicateGroups: duplicates.length ? duplicates[0].groups : 0});
if (duplicates.length) quit(2);
'

