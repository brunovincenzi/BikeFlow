#!/usr/bin/env bash
set -euo pipefail

readonly MEMBERS=(mongo-1 mongo-2 mongo-3)
primary=""

for member in "${MEMBERS[@]}"; do
  is_primary="$(docker compose exec -T "$member" mongosh --quiet --eval 'db.hello().isWritablePrimary' | tr -d '[:space:]')"
  if [[ "$is_primary" == "true" ]]; then
    primary="$member"
    break
  fi
done
if [[ -z "$primary" ]]; then
  echo "No primary found in the BikeFlow Compose project" >&2
  exit 1
fi

before_count="$(docker compose exec -T "$primary" mongosh --quiet --eval 'db.getSiblingDB("bikeflow").telemetry.countDocuments({})' | tr -d '[:space:]')"
echo "Primary before failure: $primary; documents: $before_count"
docker compose stop "$primary"

deadline=$((SECONDS + 60))
new_primary=""
while (( SECONDS < deadline )); do
  for member in "${MEMBERS[@]}"; do
    [[ "$member" == "$primary" ]] && continue
    is_primary="$(docker compose exec -T "$member" mongosh --quiet --eval 'db.hello().isWritablePrimary' 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$is_primary" == "true" ]]; then
      new_primary="$member"
      break 2
    fi
  done
  sleep 2
done

if [[ -z "$new_primary" ]]; then
  docker compose start "$primary"
  echo "No new primary elected within 60 seconds" >&2
  exit 1
fi

after_count="$(docker compose exec -T "$new_primary" mongosh --quiet --eval 'db.getSiblingDB("bikeflow").telemetry.countDocuments({})' | tr -d '[:space:]')"
duplicate_groups="$(docker compose exec -T "$new_primary" mongosh --quiet --eval '
  const result = db.getSiblingDB("bikeflow").telemetry.aggregate([
    {$group: {_id: "$event_id", count: {$sum: 1}}},
    {$match: {count: {$gt: 1}}},
    {$count: "groups"}
  ]).toArray();
  print(result.length ? result[0].groups : 0);
' | tr -d '[:space:]')"

echo "New primary: $new_primary; documents: $after_count; duplicated event_id groups: $duplicate_groups"
docker compose start "$primary"

if (( after_count < before_count )); then
  echo "Document count decreased after failover" >&2
  exit 1
fi
if [[ "$duplicate_groups" != "0" ]]; then
  echo "Duplicate event_id values detected" >&2
  exit 1
fi

for attempt in {1..30}; do
  member_count="$(docker compose exec -T "$new_primary" mongosh --quiet --eval 'rs.status().members.filter(m => m.health === 1).length' | tr -d '[:space:]')"
  [[ "$member_count" == "3" ]] && break
  sleep 2
done
if [[ "$member_count" != "3" ]]; then
  echo "The restarted member did not rejoin within 60 seconds" >&2
  exit 1
fi
docker compose exec -T "$new_primary" mongosh --quiet --eval 'printjson(rs.status().members.map(m => ({name: m.name, state: m.stateStr, health: m.health})))'
