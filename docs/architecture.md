# Architettura e decisioni progettuali

## Contesto e obiettivi

BikeFlow è un sistema distribuito didattico, non una piattaforma di produzione. Il suo confine
è intenzionalmente ridotto: un produttore di eventi, un servizio HTTP stateless e una base dati
replicata. Questo permette di osservare concetti come identità, replica, elezione, retry,
idempotenza, bilanciamento e persistenza senza introdurre Kafka, MQTT, Spark o servizi cloud.

## Flusso di un evento

1. Il simulatore sceglie una bicicletta e genera un evento valido con UUID e timestamp UTC.
2. In caso di retry mantiene immutati sia UUID sia payload.
3. Il Service inoltra la richiesta a una replica disponibile del backend.
4. Pydantic rifiuta campi extra, identificatori non validi, coordinate fuori intervallo,
   percentuali non intere, status sconosciuti e timestamp privi di timezone.
5. Il repository PyMongo tenta direttamente l'inserimento con write concern `majority`.
6. Se l'indice univoco segnala un duplicato, rilegge il documento: un payload uguale è un retry
   idempotente; un payload differente è una collisione e produce `409`.
7. Il driver scopre il primary dal replica set e può ritentare scritture compatibili dopo
   un'elezione.

MongoDB memorizza i datetime BSON al millisecondo. Il modello canonicalizza perciò il timestamp
UTC a tale precisione prima del confronto idempotente; altrimenti un retry con microsecondi
potrebbe sembrare diverso dopo la serializzazione.

## Perché il backend usa un Deployment

Il backend non conserva stato locale autorevole. Ogni replica usa la stessa configurazione e
accede a MongoDB; può essere sostituita senza perdere dati. Un Deployment esprime proprio questo
modello: repliche intercambiabili, rollout dichiarativi e ricreazione automatica dei Pod. Il
Service ClusterIP seleziona i Pod ready e offre un endpoint stabile senza esporne nomi o IP.

Due repliche consentono di mostrare che la cancellazione di un Pod non interrompe necessariamente
il servizio e che il controller ripristina il numero desiderato. La liveness probe controlla il
processo; la readiness probe include un ping MongoDB e impedisce di inviare traffico a un Pod che
non può servire correttamente le richieste.

## Perché MongoDB usa uno StatefulSet

I membri MongoDB non sono intercambiabili nello stesso senso del backend. Ogni membro ha:

- identità ordinale stabile (`mongo-0`, `mongo-1`, `mongo-2`);
- nome DNS stabile fornito dal headless Service;
- volume persistente proprio creato da `volumeClaimTemplates`;
- ruolo corrente nel protocollo del replica set.

Lo StatefulSet mantiene l'associazione tra identità e PVC anche quando un Pod viene ricreato.
Un Deployment non garantirebbe questa corrispondenza e renderebbe fragile la configurazione dei
membri del replica set.

## Service, headless Service e PVC

Il normale Service `backend` ha un IP virtuale stabile e bilancia le connessioni verso le repliche
ready. Nasconde al client il turnover dei Pod applicativi.

Il headless Service `mongo` ha `clusterIP: None`: non fornisce un singolo IP bilanciato, ma record
DNS per ogni Pod dello StatefulSet. MongoDB usa questi nomi stabili nella propria configurazione
e il driver riceve la topologia completa.

Ogni PersistentVolumeClaim richiede storage separato. Il PVC sopravvive alla sostituzione del Pod,
mentre il container filesystem no. Replica e persistenza sono complementari: i tre PVC mantengono
copie indipendenti del dataset, ma non sostituiscono un backup esterno.

## Primary, secondary, replica ed elezione

Il replica set contiene tre repliche dello stesso dataset. In un dato istante un solo membro è
primary e accetta le scritture; gli altri sono secondary e applicano l'oplog. Il write concern
`majority` considera confermata una scrittura dopo l'ack della maggioranza, riducendo il rischio
che un dato confermato venga perso durante il cambio di primary.

Quando il primary non è più raggiungibile, i membri rimasti eseguono un'elezione. Con tre votanti,
due membri formano ancora la maggioranza e uno può diventare primary. Con un solo membro rimasto
non c'è maggioranza e le scritture si fermano: la replica non equivale a disponibilità illimitata.
`retryWrites=true` aiuta il driver durante transizioni brevi, mentre l'idempotenza a livello
applicativo copre anche retry del client che non conosce l'esito precedente.

## Self-healing applicativo e persistenza

Il self-healing del Deployment ristabilisce il numero di processi desiderato. Non recupera dati
che fossero stati salvati solo nel filesystem effimero di un container. La persistenza è compito
di PVC e MongoDB; la replica rende disponibili più copie e permette l'elezione. In modo simmetrico,
un PVC da solo conserva byte ma non crea un nuovo processo né garantisce un endpoint disponibile.

Queste proprietà vanno valutate separatamente:

| Proprietà | Meccanismo principale |
|---|---|
| Ricreazione backend | Deployment controller |
| Esclusione dal traffico non pronto | readiness probe + Service |
| Identità Mongo stabile | StatefulSet + headless Service |
| Dati oltre la vita del Pod | PVC |
| Copie e failover database | replica set MongoDB |
| Retry senza duplicazione | UUID + indice univoco + logica applicativa |

## Idempotenza

Una richiesta può raggiungere il database e perdere la risposta: il client non sa se ritentare
creerà un secondo record. `event_id` è la chiave di idempotenza. L'indice univoco è l'arbitro
atomico anche quando più repliche backend elaborano contemporaneamente lo stesso UUID. Un semplice
controllo “find poi insert” sarebbe soggetto a race; BikeFlow tenta l'insert e gestisce
`DuplicateKeyError`.

Il conflitto tra stesso UUID e payload differente non viene mascherato: è un errore semantico,
perché non è possibile sapere quale versione rappresenti l'evento corretto.

## Disponibilità e consistenza

Le scritture usano maggioranza e le letture mantengono la preferenza PyMongo predefinita per il
primary. La scelta semplifica il ragionamento su letture successive alla scrittura, accettando una
breve indisponibilità durante l'elezione. Non sono implementate transazioni multi-documento perché
ogni evento è indipendente e atomico.

## Sicurezza e osservabilità

I log sono JSON su standard output e includono timestamp, livello, logger, request ID e dati
contestuali selezionati. FastAPI genera OpenAPI. Le probe distinguono processo vivo e dipendenze
pronte. Non sono incluse autenticazione MongoDB, TLS, gestione dei secret o una piattaforma di
metriche: aggiungerle sarebbe necessario fuori da un laboratorio locale.

## Limite del singolo computer

kind, Minikube e Docker Compose eseguono tutti i processi sullo stesso host fisico. Possono
dimostrare il guasto di un processo, container o Pod e una nuova elezione logica. Non dimostrano
tolleranza a perdita di alimentazione, disco, rete o macchina: un guasto dell'host colpisce tutte
le repliche e spesso tutti i volumi. Anche l'anti-affinity è solo preferenziale e su un cluster a
singolo nodo non separa fisicamente i membri.

## Compromesso didattica-produzione

Le immagini hanno versioni esplicite, i processi applicativi usano utenti non root, le risorse
Kubernetes hanno request/limit e le operazioni distruttive sono confinate al namespace. Sono
scelte abbastanza realistiche da sostenere una discussione tecnica.

Restano volutamente fuori: autenticazione e TLS, Secret, backup/restore testati, replica su zone
diverse, PodDisruptionBudget, autoscaling, rate limiting, schema migration, retention, sharding,
monitoring e alerting. Il risultato è comprensibile e riproducibile, ma non va presentato come
configurazione pronta per produzione.

