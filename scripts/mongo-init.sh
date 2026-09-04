#!/usr/bin/env bash
set -euo pipefail

MONGO_DIRECT_URI="mongodb://mongo-1:27017/admin?directConnection=true"
MONGO_REPLICA_URI="mongodb://mongo-1:27017,mongo-2:27017,mongo-3:27017/admin?replicaSet=rs0"

until mongosh "${MONGO_DIRECT_URI}" --quiet --eval 'quit(db.adminCommand({ping: 1}).ok ? 0 : 1)'; do
  echo "Waiting for mongo-1..."
  sleep 2
done

mongosh "${MONGO_DIRECT_URI}" --quiet <<'JAVASCRIPT'
try {
  const current = rs.conf();
  if (current.members.length !== 3) {
    throw new Error(`Replica set exists with ${current.members.length} members; expected 3`);
  }
  print("Replica set already configured with three members");
} catch (error) {
  if (error.codeName === "NotYetInitialized" || error.code === 94) {
    rs.initiate({
      _id: "rs0",
      members: [
        {_id: 0, host: "mongo-1:27017", priority: 2},
        {_id: 1, host: "mongo-2:27017", priority: 1},
        {_id: 2, host: "mongo-3:27017", priority: 1}
      ]
    });
    print("Replica set initialization requested");
  } else {
    throw error;
  }
}
JAVASCRIPT

until mongosh "${MONGO_REPLICA_URI}" --quiet --eval 'quit(db.hello().isWritablePrimary ? 0 : 1)'; do
  echo "Waiting for a primary election..."
  sleep 2
done

mongosh "${MONGO_REPLICA_URI}" --quiet --eval '
    const status = rs.status();
    if (status.members.length !== 3) quit(2);
    print(`Replica set ready; primary: ${status.members.find(m => m.stateStr === "PRIMARY").name}`);
  '
