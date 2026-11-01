# RegulaMed — prototipo di tesi

Dashboard di consultazione in sola lettura di un campione di informazioni regolatorie su medicinali EMA, AIFA e Gazzetta Ufficiale.

**Avvertenza:** demo accademica. Le classificazioni mostrate sono associate a fonti e date specifiche; non certificano la rimborsabilità vigente, non forniscono raccomandazioni mediche e non sostituiscono le fonti ufficiali.

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

- EMA aggiorna solo i dati dei medicinali già presenti nel campione, da JSON ufficiale.
- AIFA aggiorna anagrafica campione, liste A/H storiche e C(nn) dal sito ufficiale. Se un CSV non è disponibile, **solo AIFA** fallisce; EMA e GU restano indipendenti.
- Gazzetta esamina i sommari recenti di Serie Generale per medicinali del campione, acquisisce il testo disponibile e gli eventuali allegati HTML. Nuove coppie EU/AIC sono **evidenze da revisionare**, mai match automaticamente verificati. Non è una ricerca completa di tutti gli atti, né un certificato di rimborsabilità corrente.
- Se una fonte fallisce, rimane online l'ultimo archivio valido. Errori di rete e formati non riconosciuti non sono aggirati con dati inventati.
- Il workflow storico [aggiorna-regulamed.yml](.github/workflows/aggiorna-regulamed.yml) non è più pianificato, ma resta richiamabile manualmente per compatibilità: **non usarlo** come aggiornamento ordinario.

**Collaudo:** i nuovi workflow devono superare almeno una prima esecuzione reale nella scheda Actions. Prima di allora, la loro presenza nel repository non prova che abbiano scaricato e pubblicato nuovi dati. La dashboard mostra "Da verificare" per una fonte senza rapporto di successo.

Link: [Esecuzioni GitHub Actions](https://github.com/MarcoFabozzi/regulamed-demo/actions).


## Ampliamento controllato EMA (fase database)

La pipeline EMA indipendente usa `catalogo_ema.py` per estendere gradualmente da **30 a 300** medicinali mantenendo i 30 ID precedenti, i link documentati, gli eventi e gli allegati. L'importatore legge dal dataset ufficiale EMA (più di 2.300 record disponibili) ma **non** pubblica ancora l'intero catalogo: 300 è un campione intermedio per validare copertura e prestazioni.

Le nuove versioni EMA si archiviano in `ema_revisioni_snapshot` con hash del record, prima/ultima osservazione e ID dell'acquisizione. Sono **osservazioni dei file importati**, non nuove autorizzazioni o decreti. Il campo `data_decisione_ue` esistente può indicare una decisione successiva; non deve essere presentato come data della prima autorizzazione.

Per evitare corruzioni, se il dataset non contiene più un EMA ID preesistente o ne modifica il nome, il processo fallisce senza pubblicare il database. Le nuove associazioni AIFA non sono dedotte automaticamente dall'EMA: verranno candidate solo quando sarà disponibile un nuovo download AIFA validato. L'archivio AIFA può quindi restare parziale anche dopo l'espansione EMA.

Test della migrazione, senza rete: `python -m unittest -v test_catalogo_ema.py`. Sono inclusi nel workflow EMA. Il codice è presente su GitHub, ma **l'espansione sarà effettiva soltanto dopo una prima esecuzione del workflow EMA completata con successo**, verificando il report `risultati/stato_fonti/ema.json` e il conteggio nel database.


## Tre flussi AIFA separati

Il workflow combinato AIFA non è più pianificato. Il modulo `aifa_componenti.py` separa anagrafica confezioni (CSV diretto ufficiale), liste A/H (due file con la stessa data di riferimento) ed elenco storico C(nn). Ogni componente ha proprio workflow e report in `risultati/stato_fonti/`, ritenta ogni ora dopo i fallimenti e aggiorna solo i propri dati. I collegamenti EU-AIC già documentati non vengono alterati. Le classi A/H e C(nn) sono informazioni storiche, non certificazione di rimborsabilità vigente.

Test offline: `python -m unittest -v test_aifa_componenti.py`. Il collaudo su GitHub Actions delle tre nuove pipeline è ancora necessario.
