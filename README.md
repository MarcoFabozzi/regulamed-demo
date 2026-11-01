# FarmaQuery — prototipo di tesi

Dashboard di consultazione in sola lettura di un campione di informazioni regolatorie su medicinali EMA, AIFA e Gazzetta Ufficiale.

**Avvertenza:** demo accademica. Le classificazioni mostrate sono associate a fonti e date specifiche; non certificano la rimborsabilità vigente, non forniscono raccomandazioni mediche e non sostituiscono le fonti ufficiali.

## Identita del progetto e pubblicazione

Il repository pubblico e' stato rinominato in
[MarcoFabozzi/farmaquery](https://github.com/MarcoFabozzi/farmaquery)
e l'applicazione si presenta come **FarmaQuery**. Il codice di avvio resta
`dashboard_regolatorio.py` per evitare cambiamenti non necessari al deployment.

Il sito viene distribuito separatamente tramite Streamlit Community Cloud.
Il nome pubblico dell'app Streamlit e il relativo URL vanno controllati
dal pannello di deployment, poiche' possono essere diversi dal nome GitHub.

I workflow GitHub Actions utilizzano `github.repository == 'MarcoFabozzi/farmaquery'`
per gli aggiornamenti indipendenti. Il database e l'archivio AIFA restano
nei rispettivi percorsi `risultati/`, senza rigenerazione dovuta alla rinomina.

## Avvio

L'app richiede questi file nella radice del repository:
- `dashboard_regolatorio.py`
- `qualita_database.py`
- `requirements.txt`
- `risultati/farmaci_automatico.sqlite`

Avviare con `streamlit run dashboard_regolatorio.py`.

## Origine dei dati

Il database è uno **snapshot** locale generato dalla pipeline del progetto di tesi. Non viene aggiornato automaticamente sul server Streamlit. I collegamenti candidati EMA–AIFA sono distinti dai collegamenti documentati.

Le nuove versioni dello snapshot devono essere rigenerate e revisionate prima della pubblicazione.


## Aggiornamenti indipendenti (novembre 2026)

Sono configurati tre workflow distinti, tutti programmati ogni ora in UTC:
- [EMA](.github/workflows/aggiorna-ema.yml), minuto 23
- [AIFA anagrafica](.github/workflows/aggiorna-aifa-anagrafica.yml), minuto 31
- [AIFA A/H](.github/workflows/aggiorna-aifa-ah.yml), minuto 41
- [AIFA C(nn)](.github/workflows/aggiorna-aifa-cnn.yml), minuto 51
- [Gazzetta Ufficiale](.github/workflows/aggiorna-gazzetta.yml), minuto 43

Ogni fonte è acquisita **una volta al giorno con successo**, e ritentata ogni ora finché non riesce. L'ora di taglio giornaliera è 05:00 UTC. GitHub può ritardare/saltare occasionalmente le esecuzioni pianificate.

Tutti aggiornano **in sequenza** il medesimo archivio `risultati/farmaci_automatico.sqlite` usando un lock di concorrenza GitHub e una copia temporanea verificata. Gli aggiornamenti indipendenti scrivono la data di successo in `risultati/stato_fonti/{ema,aifa,gazzetta}.json`. Non interpretare queste date come date di efficacia normativa.

- EMA acquisisce tutti i medicinali presenti nel JSON ufficiale, conservando i dati precedenti e le versioni osservate.
- AIFA aggiorna anagrafica campione, liste A/H storiche e C(nn) dal sito ufficiale. Se un CSV non è disponibile, **solo AIFA** fallisce; EMA e GU restano indipendenti.
- Gazzetta esamina i sommari recenti di Serie Generale per medicinali del campione, acquisisce il testo disponibile e gli eventuali allegati HTML. Nuove coppie EU/AIC sono **evidenze da revisionare**, mai match automaticamente verificati. Non è una ricerca completa di tutti gli atti, né un certificato di rimborsabilità corrente.
- Se una fonte fallisce, rimane online l'ultimo archivio valido. Errori di rete e formati non riconosciuti non sono aggirati con dati inventati.
- Il workflow storico [aggiorna-farmaquery.yml](.github/workflows/aggiorna-farmaquery.yml) non è più pianificato, ma resta richiamabile manualmente per compatibilità: **non usarlo** come aggiornamento ordinario.

**Collaudo:** i nuovi workflow devono superare almeno una prima esecuzione reale nella scheda Actions. Prima di allora, la loro presenza nel repository non prova che abbiano scaricato e pubblicato nuovi dati. La dashboard mostra "Da verificare" per una fonte senza rapporto di successo.

Link: [Esecuzioni GitHub Actions](https://github.com/MarcoFabozzi/farmaquery/actions).


## Ampliamento controllato EMA (fase database)

La pipeline EMA indipendente usa `catalogo_ema.py` per estendere gradualmente da **30 a 300** medicinali mantenendo i 30 ID precedenti, i link documentati, gli eventi e gli allegati. L'importatore legge dal dataset ufficiale EMA (più di 2.300 record disponibili) ma **non** pubblica ancora l'intero catalogo: 300 è un campione intermedio per validare copertura e prestazioni.

Le nuove versioni EMA si archiviano in `ema_revisioni_snapshot` con hash del record, prima/ultima osservazione e ID dell'acquisizione. Sono **osservazioni dei file importati**, non nuove autorizzazioni o decreti. Il campo `data_decisione_ue` esistente può indicare una decisione successiva; non deve essere presentato come data della prima autorizzazione.

Per evitare corruzioni, se il dataset non contiene più un EMA ID preesistente o ne modifica il nome, il processo fallisce senza pubblicare il database. Le nuove associazioni AIFA non sono dedotte automaticamente dall'EMA: verranno candidate solo quando sarà disponibile un nuovo download AIFA validato. L'archivio AIFA può quindi restare parziale anche dopo l'espansione EMA.

Test della migrazione, senza rete: `python -m unittest -v tests.test_catalogo_ema`. Sono inclusi nel workflow EMA. Il codice è presente su GitHub, ma **l'espansione sarà effettiva soltanto dopo una prima esecuzione del workflow EMA completata con successo**, verificando il report `risultati/stato_fonti/ema.json` e il conteggio nel database.


## Tre flussi AIFA separati

Il workflow combinato AIFA non è più pianificato. Il modulo `aifa_componenti.py` separa anagrafica confezioni (CSV diretto ufficiale), liste A/H (due file con la stessa data di riferimento) ed elenco storico C(nn). Ogni componente ha proprio workflow e report in `risultati/stato_fonti/`, ritenta ogni ora dopo i fallimenti e aggiorna solo i propri dati. I collegamenti EU-AIC già documentati non vengono alterati. Le classi A/H e C(nn) sono informazioni storiche, non certificazione di rimborsabilità vigente.

Test offline: `python -m unittest -v tests.test_aifa_componenti`. Il collaudo su GitHub Actions delle tre nuove pipeline è ancora necessario.


## Audit offline della qualita del database

Il file `verifica_qualita_base_dati.py` esamina in sola lettura l'ultimo SQLite pubblicato e gli allegati GU presenti nel repository. Controlla `PRAGMA integrity_check`, chiavi esterne, ID orfani, conflitti delle classi A/H nella stessa data, completezza dello storico EMA, hash e percorsi degli allegati, formati EU/AIC e ultimo successo per fonte. Distingue errori bloccanti (strutturali) da avvertenze (AIFA non aggiornato, bassa copertura documentale). Non valuta la rimborsabilita vigente.

Il workflow [verifica-qualita.yml](.github/workflows/verifica-qualita.yml) e programmato una volta al giorno, senza scaricare nuove fonti. Pubblica `risultati/qualita_strutturale.json` solo se tutti i controlli bloccanti sono superati. Il rapporto appare nella scheda *Qualita e report* della dashboard. Da collaudare con una prima esecuzione manuale GitHub Actions; un workflow salvato non equivale ad audit riuscito.


## Catalogo EMA completo

Dal novembre 2026 la procedura EMA importa **tutti** i record del catalogo ufficiale che superano il parser, senza limitarsi ai 300 del campione iniziale. Il numero effettivo varia con gli aggiornamenti EMA e va letto da `risultati/stato_fonti/ema.json`. Una risposta con meno di 2.000 record, un ID esistente mancante o un nome associato a un ID esistente cambiato blocca la pubblicazione per revisione manuale. Questo controllo è prudenziale e potrà essere adeguato se EMA modifica legittimamente il catalogo. Le confezioni AIFA, i collegamenti verificati e la Gazzetta non sono modificati da questa importazione.

Il codice è predisposto, ma il conteggio definitivo sarà confermato solo dopo una nuova esecuzione riuscita del workflow EMA.


## Anagrafica AIFA integrale in archivio separato

L'aggiornamento `aifa_anagrafica` ora produce anche `risultati/aifa_confezioni_complete.sqlite.gz`, un archivio SQLite compresso delle confezioni univoche del CSV ufficiale (circa 160.314 nell'ultima acquisizione disponibile). L'archivio non sostituisce `farmaci_automatico.sqlite`, che conserva i record pertinenti ai collegamenti EMA e GU. La dashboard decomprime l'archivio integrale in una cache temporanea soltanto quando serve e permette ricerche parametrizzate per nome, principio attivo e AIC. Non deduce automaticamente associazioni né classi attuali. Il file compresso viene pubblicato solo dopo controllo integrità, assenza di AIC duplicate e test offline. Il conteggio effettivo sarà confermato soltanto dopo la prima esecuzione riuscita del nuovo workflow.


## Gazzetta: screening incrementale dei testi

La pipeline non si limita più ai titoli che nominano direttamente un medicinale. Tra gli atti farmaceutici dei fascicoli recenti esamina progressivamente il testo (fino a 80 nuovi atti per esecuzione), dando precedenza ai titoli nominativi. La tabella `gu_screening_atti` conserva i codici già controllati e i relativi esiti, anche quando non sono emerse corrispondenze con il catalogo. Sono archiviati solo gli atti in cui il testo cita un nome EMA oppure una AIC già nel database relazionale. I nuovi documenti restano `DA_REVISIONARE`; l'individuazione per nome o AIC non prova una corrispondenza autorizzativa, né l'attualità della rimborsabilità. La scansione riguarda i sommari farmaceutici recenti della Serie Generale, non costituisce copertura storica integrale. I risultati e i conteggi andranno collaudati con una nuova esecuzione riuscita del workflow Gazzetta.


## Perimetro finale della base dati (tesi triennale)

La base informativa è sufficiente per iniziare la sperimentazione accademica: catalogo EMA acquisito (2.357 medicinali), anagrafica AIFA integrale in archivio separato (160.314 confezioni), 60 atti GU nel database e un rapporto di confronto su 100 coppie EU–AIC. La sezione **Gazzetta e allegati** della dashboard mostra una tabella filtrabile dei collegamenti già documentati e delle corrispondenze nominali da verificare. Le 90 corrispondenze per nome **non** vengono trasformate in associazioni confermate. La fase successiva sarà un benchmark piccolo e riproducibile per Text-to-SQL, RAG e TAG, non la ricostruzione esaustiva di tutto il diritto farmaceutico italiano.


## Test automatici

I 7 file di test sono organizzati nella cartella [`tests/`](tests/), separata dal codice applicativo. GitHub Actions li esegue prima degli aggiornamenti e degli audit pertinenti. Per eseguirli tutti in locale, dalla radice del repository: `python -m unittest discover -s tests -t . -v`. Non eliminare la cartella `tests/`: i workflow dipendono da questi controlli.
