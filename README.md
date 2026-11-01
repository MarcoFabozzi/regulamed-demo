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
- [AIFA](.github/workflows/aggiorna-aifa.yml), minuto 33
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
