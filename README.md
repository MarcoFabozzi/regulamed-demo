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
