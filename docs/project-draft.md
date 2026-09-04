# Bozza di relazione d'esame — BikeFlow

## Abstract

BikeFlow è un prototipo di backend distribuito per la raccolta di telemetria di una flotta
bike-sharing simulata. Il progetto è stato sviluppato con Python, FastAPI, Pydantic, PyMongo,
MongoDB, Docker Compose e Kubernetes. L'obiettivo non è costruire una piattaforma industriale,
ma rendere osservabili proprietà fondamentali dei sistemi distribuiti: replica, elezione,
self-healing, identità stabile, persistenza, retry e idempotenza.

## Problema

Ogni bicicletta produce eventi contenenti identificativo univoco, posizione, stazione, batteria,
stato e istante di registrazione. La rete o il server possono interrompersi dopo che un evento è
stato salvato ma prima che il client riceva risposta. Inoltre, un'istanza applicativa o il primary
del database possono terminare. Il sistema deve quindi validare i dati, evitare duplicati durante
i retry, mantenere più copie del dataset e recuperare automaticamente i processi terminati.

## Requisiti principali

Il backend espone operazioni per inserire e consultare eventi, ricavare l'ultimo stato e la storia
di una bicicletta, aggregare statistiche per stazione e segnalare liveness/readiness. Il simulatore
è parametrico e produce anche duplicati e stati anomali. MongoDB opera come replica set reale a
tre membri. Compose fornisce un avvio locale con un comando; Kubernetes rappresenta i componenti
con controller e storage adeguati.

## Modello dei dati

`event_id` è un UUID e costituisce la chiave di idempotenza. `bike_id` e `station_id` sono stringhe
limitate e validate. Latitudine e longitudine rispettano gli intervalli geografici; la batteria è
un intero tra 0 e 100; lo stato appartiene a `available`, `in_use`, `maintenance`, `offline`.
`recorded_at` richiede timezone, viene convertito in UTC e ridotto alla precisione BSON del
millisecondo.

Il database possiede un indice univoco su `event_id` e un indice composto su `bike_id` e
`recorded_at` discendente. Il primo impedisce duplicati in modo atomico anche tra richieste
concorrenti; il secondo accelera latest e history.

## Architettura applicativa

Il simulatore invia JSON tramite HTTP e applica un numero limitato di retry con backoff
esponenziale e jitter. Un retry riutilizza esattamente lo stesso evento. FastAPI genera OpenAPI e
delega la validazione a Pydantic. La persistenza è isolata in un repository PyMongo, scelta che
permette test unitari con memoria locale e test d'integrazione separati con MongoDB reale.

Il backend non ha stato locale: Kubernetes usa un Deployment a due repliche. Il Service ClusterIP
offre un nome stabile e seleziona solo repliche ready. La liveness probe verifica il processo;
la readiness probe esegue un ping al database.

MongoDB richiede invece identità e disco stabili. Lo StatefulSet assegna gli ordinali `mongo-0`,
`mongo-1`, `mongo-2`; il headless Service crea record DNS individuali e ogni replica riceve un PVC
distinto. Un Job inizializza `rs0` in modo idempotente.

## Idempotenza e concorrenza

La strategia non usa la sequenza vulnerabile “verifica poi inserisci”. Il repository esegue
direttamente `insert_one`. Se due richieste arrivano insieme, l'indice univoco ne accetta una e
MongoDB solleva `DuplicateKeyError` per l'altra. Il backend rilegge il record: se il payload è
uguale risponde `duplicate`, se è differente segnala conflitto HTTP 409.

Questa distinzione è rilevante perché non ogni ripetizione di UUID è un retry legittimo. Il
confronto protegge da produttori difettosi che riusano una chiave per informazioni diverse.

## Replica ed elezione MongoDB

Il primary riceve le scritture, mentre i secondary replicano l'oplog. Con tre votanti il sistema
continua a eleggere un primary finché due membri comunicano. Le scritture usano write concern
`majority`: la conferma richiede la maggioranza e offre una garanzia più forte durante il failover.
Il driver PyMongo usa l'URI completo del replica set, scopre i ruoli e supporta retryable writes.

Una replica è una copia coordinata del dataset, non un backup. Un errore logico può propagarsi a
tutte le repliche. Per un sistema reale servirebbero backup separati e prove di restore.

## Self-healing e persistenza

Il Deployment ricrea un Pod backend terminato: questo è self-healing applicativo. Non conserva i
dati del filesystem effimero. StatefulSet e PVC mantengono l'associazione tra replica MongoDB e
disco; il replica set mantiene copie e sceglie un nuovo primary. Sono meccanismi distinti e
complementari.

## Strategia di test

I test unitari coprono validazione, inserimento, idempotenza, collisione, concorrenza, latest,
history, filtri, health/readiness, aggregazioni e retry. Usano un repository in memoria protetto da
lock. I test d'integrazione vengono eseguiti nella rete Compose e provocano realmente violazioni
concorrenti dell'indice MongoDB; verificano anche che `rs0` abbia tre membri e un solo primary.

Gli esperimenti operativi cancellano un Pod backend o il Pod primary MongoDB, misurano i tempi di
recupero e controllano conteggio e unicità dei dati. Gli script sono limitati al namespace
`bikeflow` e validano i target.

## Risultati da riportare

Inserire qui i risultati ottenuti seguendo `docs/experiments.md`:

- configurazione hardware e software: **[da compilare]**;
- throughput e durata del carico: **[da compilare]**;
- tempo medio di ricreazione backend: **[da compilare]**;
- tempo medio di elezione MongoDB: **[da compilare]**;
- errori HTTP durante i guasti: **[da compilare]**;
- documenti prima/dopo e gruppi duplicati: **[da compilare]**.

È importante distinguere osservazione e conclusione. Per esempio, il ritorno a due Pod backend
dimostra il funzionamento del controller; non dimostra da solo che nessuna richiesta sia fallita
durante il transitorio.

## Limiti

Il prototipo non include autenticazione, TLS, backup, autoscaling, PodDisruptionBudget, rate
limiting o monitoring esterno. Le statistiche per stazione non sono progettate per dataset
industriali. Soprattutto, Docker Desktop, kind e Minikube risiedono su un solo computer: separano
processi e dischi logici, ma condividono alimentazione, hardware e spesso storage. Un guasto fisico
totale interromperebbe tutte le repliche.

Questi limiti sono un compromesso intenzionale. L'aggiunta di operator MongoDB, certificati,
Secret, backup e osservabilità aumenterebbe il realismo ma sposterebbe l'attenzione dai concetti
centrali e renderebbe più difficile riprodurre il laboratorio.

## Conclusioni

BikeFlow mostra come un servizio stateless possa scalare e autoripararsi con un Deployment, mentre
un database stateful richieda identità, persistenza e un protocollo di replica. La chiave UUID e
l'indice univoco rendono sicuri i retry anche con concorrenza; maggioranza ed elezione mantengono
il database operativo dopo il guasto di un membro. Il progetto offre quindi una base compatta per
discutere sia le garanzie ottenute sia quelle che un cluster locale non può fornire.

