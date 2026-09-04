# Protocollo degli esperimenti

## Scopo

Gli esperimenti verificano quattro affermazioni distinte:

1. il Deployment ripristina una replica backend eliminata;
2. il replica set elegge un primary diverso quando quello corrente termina;
3. i dati confermati restano disponibili dopo l'elezione;
4. retry e richieste duplicate non creano più documenti per lo stesso `event_id`.

Eseguire gli esperimenti solo sul namespace `bikeflow` o sul progetto Compose di questa directory.
Prima di iniziare registrare versioni, risorse della macchina e parametri del carico.

## Metriche

- tempo di ricreazione backend: dalla cancellazione a due Pod ready;
- tempo di elezione: dalla cancellazione/stop del primary alla scoperta di un primary diverso;
- richieste tentate, create, duplicate e fallite dal simulatore;
- latenza osservata, se raccolta con uno strumento esterno;
- conteggio documenti prima e dopo il guasto;
- numero di gruppi con `event_id` duplicato;
- eventuali risposte HTTP `5xx` durante la finestra di guasto.

Usare l'orologio monotono dello script o timestamp UTC. Ripetere ogni prova almeno tre volte prima
di trarre conclusioni.

## Preparazione Compose

```bash
docker compose up --build -d
curl --fail http://localhost:8000/health/ready
docker compose --profile test run --rm tests
```

Generare un dataset iniziale:

```bash
SIM_BIKES=50 SIM_EVENTS_PER_SECOND=10 SIM_DURATION_SECONDS=60 \
  SIM_DUPLICATE_PROBABILITY=0.10 scripts/experiments/load.sh
```

### Failover Compose

```bash
scripts/experiments/compose-mongo-failover.sh
```

Lo script individua il primary senza assumere che sia `mongo-1`, registra il conteggio, ferma solo
quel servizio, attende un primary diverso, verifica dati e unicità, quindi riavvia il membro. Il
test ha successo solo se il conteggio non diminuisce e non esistono gruppi duplicati.

## Preparazione Kubernetes

Dopo il deployment descritto nel README:

```bash
kubectl -n bikeflow get pods,pvc
kubectl -n bikeflow wait --for=condition=complete job/mongo-rs-init --timeout=180s
kubectl -n bikeflow rollout status deployment/backend --timeout=180s
```

In un terminale mantenere il port-forward:

```bash
kubectl -n bikeflow port-forward service/backend 8000:8000
```

In un secondo terminale generare carico. Lo script Compose usa la rete Compose; per Kubernetes
si può usare direttamente il simulatore locale:

```bash
.venv/bin/python -m simulator \
  --backend-url http://localhost:8000 \
  --bikes 50 \
  --events-per-second 10 \
  --duration-seconds 180 \
  --seed 42 \
  --duplicate-probability 0.10 \
  --anomaly-probability 0.05
```

Oppure applicare `k8s/optional/simulator-job.yaml` dopo aver caricato l'immagine nel cluster.

## Esperimento A: self-healing backend

1. Verificare che esistano due Pod backend ready.
2. Annotare ora UTC e nome del Pod selezionato.
3. Eseguire lo script.
4. Annotare il tempo fino al rollout completo e gli eventuali errori osservati dal carico.
5. Verificare che il nuovo Pod abbia un UID diverso.

```bash
scripts/experiments/backend-recovery.sh
kubectl -n bikeflow get pods -l app.kubernetes.io/name=backend -o wide
```

Lo script rifiuta nomi che non iniziano per `backend-` e usa sempre il namespace fisso.

## Esperimento B: elezione MongoDB

1. Generare dati e annotare il conteggio iniziale.
2. Individuare il primary interrogando ogni membro.
3. Terminare solo il Pod primary.
4. Attendere un primary con nome diverso.
5. Interrogare i dati sul nuovo primary.
6. Controllare i gruppi duplicati per `event_id`.
7. Attendere che il Pod terminato rientri ready.

```bash
scripts/experiments/mongo-failover.sh
scripts/experiments/verify-data.sh
```

Durante l'elezione sono ammissibili errori transitori. Non è ammissibile una diminuzione dei dati
già confermati con maggioranza o la comparsa di due documenti con lo stesso `event_id`.

## Query manuale di controllo

Da un Pod MongoDB:

```javascript
const database = db.getSiblingDB("bikeflow");
database.telemetry.countDocuments({});
database.telemetry.aggregate([
  {$group: {_id: "$event_id", count: {$sum: 1}}},
  {$match: {count: {$gt: 1}}},
  {$count: "groups"}
]);
```

## Scheda ambiente

| Data UTC | Host/CPU/RAM | Docker/Kubernetes | Versione MongoDB | Commit | Note |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## Risultati self-healing backend

| Prova | Pod eliminato | Inizio UTC | Due Pod ready UTC | Recupero (s) | Errori HTTP | Note |
|---:|---|---|---|---:|---:|---|
| 1 |  |  |  |  |  |  |
| 2 |  |  |  |  |  |  |
| 3 |  |  |  |  |  |  |

## Risultati failover MongoDB

| Prova | Primary iniziale | Nuovo primary | Elezione (s) | Documenti prima | Documenti dopo | Duplicati | Note |
|---:|---|---|---:|---:|---:|---:|---|
| 1 |  |  |  |  |  |  |  |
| 2 |  |  |  |  |  |  |  |
| 3 |  |  |  |  |  |  |  |

## Risultati del carico

| Prova | Bike | Eventi/s | Durata (s) | Tentati | Creati | Duplicate | Falliti | Note |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 |  |  |  |  |  |  |  |  |
| 2 |  |  |  |  |  |  |  |  |
| 3 |  |  |  |  |  |  |  |  |

## Interpretazione e cautele

La ricreazione di un Pod dimostra il controllo dello stato desiderato, non la durabilità. Il
failover di MongoDB dimostra una maggioranza disponibile nello stesso cluster, non la resilienza
al guasto dell'host fisico. Un risultato singolo non misura un Service Level Objective; serve una
serie di prove e un ambiente controllato. Conservare log e parametri insieme alle tabelle.

