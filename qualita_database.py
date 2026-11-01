#!/usr/bin/env python3
"""Audit scientifico, SOLO LETTURA, del DB EMA/AIFA/Gazzetta della tesi.

Non identifica una classificazione normativa attuale; elabora solo evidenze locali.
Nessuna dipendenza di terze parti. Compatibile con Python 3.10+.
"""
from __future__ import annotations
import argparse
import csv
from contextlib import closing
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / 'risultati' / 'farmaci_automatico.sqlite'
REQUIRED = ('ema_medicinali', 'aifa_confezioni', 'link_verificati',
            'match_candidati', 'acquisizioni')
OPTIONAL = ('classi_snapshot', 'cnn_elenco_storico', 'eventi_verificati',
            'gazzetta_atti', 'gazzetta_evidenze_aic', 'gu_allegati',
            'gu_coppie_eu_aic', 'anomalie')


def connessione_sola_lettura(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'Database inesistente: {path}')
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def presenti(con):
    return {r['name'] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def scalar(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


def conta(con, tab, names):
    # tab must be from the hardcoded safe whitelist
    if tab not in REQUIRED + OPTIONAL:
        raise ValueError('Tabella non autorizzata')
    return scalar(con, f'SELECT COUNT(*) FROM {tab}') if tab in names else 0


def audit(path=DEFAULT_DB):
    path = Path(path).resolve()
    with closing(connessione_sola_lettura(path)) as con:
        names = presenti(con)
        missing = set(REQUIRED) - names
        if missing:
            raise ValueError('Schema non compatibile. Mancano: ' + ', '.join(sorted(missing)))
        counts = {t: conta(con, t, names) for t in REQUIRED + OPTIONAL}
        ok = con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        fk_errors = len(con.execute('PRAGMA foreign_key_check').fetchall())
        meds_with_links = scalar(con, 'SELECT COUNT(DISTINCT ema_id) FROM link_verificati')
        aic_with_links = scalar(con, 'SELECT COUNT(DISTINCT aic) FROM link_verificati')
        meds_with_candidates = scalar(con, 'SELECT COUNT(DISTINCT ema_id) FROM match_candidati')
        links_missing_medicine = scalar(con, '''SELECT COUNT(*) FROM link_verificati l
            LEFT JOIN ema_medicinali e ON l.ema_id=e.ema_id WHERE e.ema_id IS NULL''')
        links_missing_aic = scalar(con, '''SELECT COUNT(*) FROM link_verificati l
            LEFT JOIN aifa_confezioni a ON l.aic=a.aic WHERE a.aic IS NULL''')
        candidates_conflict = scalar(con, '''SELECT COUNT(*) FROM
            (SELECT aic FROM match_candidati GROUP BY aic HAVING COUNT(DISTINCT ema_id)>1)''')
        gap_dates = scalar(con, 'SELECT COUNT(*) FROM ema_medicinali WHERE data_decisione_ue IS NULL OR TRIM(data_decisione_ue)=""')
        gap_indications = scalar(con, 'SELECT COUNT(*) FROM ema_medicinali WHERE indicazione_ema IS NULL OR TRIM(indicazione_ema)=""')
        snap_dates = []
        if 'classi_snapshot' in names:
            snap_dates = [dict(r) for r in con.execute('''SELECT classe, data_riferimento, COUNT(*) n
                FROM classi_snapshot GROUP BY classe, data_riferimento ORDER BY data_riferimento DESC, classe''')]
        acq = [dict(r) for r in con.execute('''SELECT tipo,origine,sha256,data_acquisizione,data_riferimento,etichetta
            FROM acquisizioni ORDER BY data_acquisizione DESC''')]
        gu_evidence_match = gu_evidence_conflict = gu_evidence_new = 0
        if 'gu_coppie_eu_aic' in names:
            for row in con.execute('SELECT eu,aic FROM gu_coppie_eu_aic'):
                rows = con.execute('SELECT codice_presentazione_ue FROM link_verificati WHERE aic=?',(row['aic'],)).fetchall()
                if any(r[0].upper() == row['eu'].upper() for r in rows):
                    gu_evidence_match += 1
                elif rows:
                    gu_evidence_conflict += 1
                else:
                    gu_evidence_new += 1
        gu_files = 0
        if 'gazzetta_atti' in names:
            for row in con.execute('SELECT testo_file FROM gazzetta_atti'):
                raw = row[0] or ''
                f = Path(raw)
                if raw and ((path.parent / f) if not f.is_absolute() else f).is_file():
                    gu_files += 1
        report_path = path.parent / 'rapporto_automatico.json'
        manifest = None
        if report_path.is_file():
            try:
                manifest = json.loads(report_path.read_text(encoding='utf8'))
            except (OSError, json.JSONDecodeError):
                pass
        return {
            'generato_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'database':str(path), 'tabelle':counts,
            'integrita': {'quick_check':ok,'errori_foreign_key':fk_errors},
            'copertura':{'medicinali_con_link':meds_with_links,'aic_con_link':aic_with_links,
                'medicinali_con_candidati':meds_with_candidates,
                'percentuale_medicinali_con_link':round(100*meds_with_links/max(counts['ema_medicinali'],1),1),
                'ema_senza_data_decisione':gap_dates,'ema_senza_indicazione':gap_indications,
                'aic_ambigue_tra_candidati':candidates_conflict,
                'link_orfani_ema':links_missing_medicine,'link_orfani_aifa':links_missing_aic},
            'evidenze_gazzetta':{'coppie_coerenti':gu_evidence_match,
                'coppie_in_conflitto':gu_evidence_conflict,
                'coppie_senza_link_precedente':gu_evidence_new,
                'testi_principali_disponibili':gu_files},
            'snapshot_ah':snap_dates,'acquisizioni':acq,
            'ultima_importazione':{
                'fine_utc':manifest.get('fine_utc'),
                'modalita':manifest.get('modalita'),
                'data_liste_ah':manifest.get('data_liste_ah'),
                'fonti': {k:v.get('url') for k,v in manifest.get('fonti',{}).items()}
            } if isinstance(manifest,dict) else None,
            'avvertenze':[
                'Il campione non rappresenta necessariamente l’intero universo EMA/AIFA.',
                'Un match per nome o principio attivo rimane candidato, non verificato.',
                'Le liste A/H sono snapshot datati; la C(nn) è un elenco storico, non lo stato corrente.',
                'L’ultima determina acquisita non certifica l’ultima determina effettivamente pubblicata.',
                'Una indicazione autorizzata EMA non equivale automaticamente a una indicazione rimborsata SSN.',
                'La data di pubblicazione non coincide necessariamente con quella di efficacia.'
            ]}


def inventario(path):
    """Catalogo 1 record/medicinale, non confondere candidati e link."""
    with closing(connessione_sola_lettura(path)) as con:
        names=presenti(con)
        gu_select = "(SELECT COUNT(*) FROM gazzetta_atti g WHERE LOWER(g.nominativi_campione) LIKE '%' || LOWER(e.nome) || '%')" if 'gazzetta_atti' in names else '0'
        data = con.execute(f'''SELECT e.ema_id, e.nome, e.principio_attivo,
               e.data_decisione_ue,
               (SELECT COUNT(*) FROM link_verificati l WHERE l.ema_id=e.ema_id) AS confezioni_documentate,
               (SELECT COUNT(*) FROM match_candidati m WHERE m.ema_id=e.ema_id) AS confezioni_candidate,
               (SELECT COUNT(*) FROM eventi_verificati v WHERE v.ema_id=e.ema_id) AS eventi_pilota,
               {gu_select} AS atti_gazzetta_compatibili_per_nome
               FROM ema_medicinali e ORDER BY e.nome, e.ema_id''').fetchall() if 'eventi_verificati' in names else con.execute('''SELECT e.ema_id,e.nome,e.principio_attivo,e.data_decisione_ue,
               (SELECT COUNT(*) FROM link_verificati l WHERE l.ema_id=e.ema_id) confezioni_documentate,
               (SELECT COUNT(*) FROM match_candidati m WHERE m.ema_id=e.ema_id) confezioni_candidate,
               0 eventi_pilota,0 atti_gazzetta_compatibili_per_nome
               FROM ema_medicinali e ORDER BY e.nome,e.ema_id''').fetchall()
        return [dict(x) for x in data]


def csv_protetto(rows):
    buffer=io.StringIO(newline='')
    keys=list(rows[0]) if rows else ['nessun_record']
    w=csv.DictWriter(buffer,fieldnames=keys)
    w.writeheader()
    for row in rows:
        w.writerow({k:("'"+str(v) if str(v or '').lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()})
    return buffer.getvalue().encode('utf-8-sig')


def relazione_md(r):
    t=r['tabelle']; c=r['copertura']; e=r['evidenze_gazzetta']
    data_ah=(r['ultima_importazione'] or {}).get('data_liste_ah') or 'non determinata'
    mode=(r['ultima_importazione'] or {}).get('modalita') or 'non documentata'
    return f'''# Stato di avanzamento – integrazione di dati regolatori farmaceutici

**Data del report (UTC):** {r['generato_utc']}  
**Origine:** database locale prodotto dalla pipeline EMA–AIFA/Gazzetta  
**Tipo di acquisizione:** {mode}

## Obiettivo
Realizzare una base dati integrata con provenienza tracciabile per interrogare autorizzazioni UE, confezioni italiane, provvedimenti e informazioni sulla rimborsabilità, come prerequisito a un assistente TAG.

## Risultati verificabili nel database locale

| Indicatore | Valore |
|---|---:|
| Medicinali EMA nel database | {t['ema_medicinali']} |
| Confezioni AIFA nel database | {t['aifa_confezioni']} |
| Coppie EMA–AIC documentate e registrate | {t['link_verificati']} |
| Medicinali con almeno un collegamento documentato | {c['medicinali_con_link']} ({c['percentuale_medicinali_con_link']}%) |
| Corrispondenze candidate NON verificate | {t['match_candidati']} |
| Snapshot A/H archiviati | {t['classi_snapshot']} |
| Righe elenco storico C(nn) | {t['cnn_elenco_storico']} |
| Atti GU presenti | {t['gazzetta_atti']} |
| Allegati GU presenti | {t['gu_allegati']} |
| Coppie EU–AIC estratte da allegati | {t['gu_coppie_eu_aic']} |
| Coppie GU coerenti con link esistenti | {e['coppie_coerenti']} |
| Coppie GU in conflitto | {e['coppie_in_conflitto']} |

**Riferimento temporale delle liste A/H:** {data_ah}. Non è la data odierna e non prova una classe vigente.

**Controllo integrità SQLite:** {'superato' if r['integrita']['quick_check'] else 'fallito'}; riferimenti FK segnalati: {r['integrita']['errori_foreign_key']}.

## Metodologia
- Importazione da fonti pubbliche, con registrazione di URL, hash e date di acquisizione.
- Riconciliazione prudente: i collegamenti nominali sono candidati; le coppie EU–AIC tratte da atti ufficiali sono registrate come evidenza documentale.
- Conservazione selettiva di eventi regolatori, snapshot A/H e metadati di pubblicazione.
- Analisi dei testi della Gazzetta senza assumere automaticamente le date di efficacia.

## Limiti rilevanti
{chr(10).join('- '+x for x in r['avvertenze'])}

## Piano di consolidamento prima di TAG
1. Estendere il campione in modo controllato e controllare copertura e duplicati dopo ogni importazione.
2. Verificare nuove coppie EU–AIC da documenti ufficiali, distinguendo inferenze e prove.
3. Migliorare l'acquisizione degli allegati e delle indicazioni realmente rimborsate.
4. Registrare in modo separato date di decisione, pubblicazione, efficacia e acquisizione.
5. Definire un insieme di domande di verifica, con risposta e fonte attesa.

*Documento generato automaticamente; non certifica il quadro regolatorio aggiornato di alcun farmaco.*
'''


def relazione_html(r):
    """Relazione HTML stampabile e priva di dipendenze esterne."""
    from html import escape
    t, c, e = r['tabelle'], r['copertura'], r['evidenze_gazzetta']
    h = lambda v: escape(str(v if v is not None else 'Non disponibile'))
    values = [('Medicinali EMA',t['ema_medicinali']),('Confezioni AIFA',t['aifa_confezioni']),
              ('Link EU–AIC documentati',t['link_verificati']),('Match non verificati',t['match_candidati']),
              ('Atti Gazzetta',t['gazzetta_atti']),('Coppie dagli allegati',t['gu_coppie_eu_aic'])]
    cards = ''.join(f'<div class="metric"><small>{h(a)}</small><strong>{h(v)}</strong></div>' for a,v in values)
    risks = ''.join(f'<li>{h(v)}</li>' for v in r['avvertenze'])
    snaps = ''.join(f'<tr><td>{h(z["classe"])}</td><td>{h(z["data_riferimento"])}</td><td>{h(z["n"])}</td></tr>' for z in r['snapshot_ah'])
    sources = ''.join(f'<tr><td>{h(z["tipo"])}</td><td>{h(z["data_acquisizione"])}</td><td>{h(z["data_riferimento"] or "—")}</td><td>{h(z["etichetta"])}</td></tr>' for z in r['acquisizioni'][:12])
    snaptable = f'<table><tr><th>Classe</th><th>Data lista</th><th>Record</th></tr>{snaps}</table>' if snaps else '<p>Nessuno snapshot A/H presente.</p>'
    srctable = f'<table><tr><th>Fonte</th><th>Acquisizione UTC</th><th>Riferimento</th><th>Nota</th></tr>{sources}</table>' if sources else '<p>Nessun metadato.</p>'
    status = 'superato' if r['integrita']['quick_check'] and r['integrita']['errori_foreign_key'] == 0 else 'da verificare'
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><title>FarmaQuery - Stato di avanzamento</title><style>
body{{font:15px/1.6 Arial,Helvetica,sans-serif;color:#193044;background:#f4f7f9;margin:0}}
main{{max-width:960px;margin:36px auto;padding:28px 44px;background:#fff;border-radius:16px;box-shadow:0 10px 30px #122e4317}}
header{{background:linear-gradient(110deg,#132d42,#167467);color:white;border-radius:12px;padding:28px 34px}}
header h1{{margin:0;font-size:28px}}header p{{margin:3px 0;color:#e4f6f1}}h2{{color:#1c4659;margin-top:29px;border-bottom:1px solid #e3ecee;padding-bottom:6px;font-size:20px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:22px}}.metric{{padding:16px;border:1px solid #e2ebef;border-radius:12px}}small{{display:block;color:#617988}}strong{{font-size:26px;color:#107e70;display:block}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border-bottom:1px solid #e3eaee;padding:9px;text-align:left;vertical-align:top}}th{{background:#eef5f6}}.note{{background:#eef9f5;border-left:4px solid #128e76;padding:13px 18px}}.warn{{background:#fff8eb;border-left:4px solid #ce8a27;padding:13px 18px}}.fine{{color:#657c89;font-size:12px}}li{{margin:7px 0}}
@media print{{body{{background:white}}main{{box-shadow:none;margin:0;padding:0}}.grid{{break-inside:avoid}}h2{{break-after:avoid}}}}</style></head>
<body><main><header><h1>FarmaQuery · Stato di avanzamento</h1><p>Integrazione di informazioni regolatorie EMA, AIFA e Gazzetta Ufficiale</p></header>
<p class="fine">Report del {h(r['generato_utc'])} (UTC). Analisi del database locale, non certificazione normativa.</p>
<h2>Obiettivo</h2><p>Costruire una base dati verificabile, con storia regolatoria selettiva e documenti di provenienza, da utilizzare successivamente nello studio sperimentale del Table-Augmented Generation.</p>
<div class="grid">{cards}</div>
<h2>Copertura e qualità</h2><p>Medicinali con almeno un collegamento EU–AIC documentato: <b>{h(c['medicinali_con_link'])}/{h(t['ema_medicinali'])} ({h(c['percentuale_medicinali_con_link'])}%)</b>. Coppie GU coerenti con link registrati: <b>{h(e['coppie_coerenti'])}</b>; in conflitto: <b>{h(e['coppie_in_conflitto'])}</b>.</p>
<p class="note">Controllo d'integrità SQLite: <b>{status}</b>. Non equivale a validazione dei provvedimenti né delle condizioni di rimborso.</p>
<h2>Snapshot AIFA A/H</h2>{snaptable}<p class="fine">Gli snapshot riflettono le date delle liste, non necessariamente lo stato corrente.</p>
<h2>Provenienza delle informazioni</h2>{srctable}
<h2>Limiti attuali</h2><div class="warn"><ul>{risks}</ul></div>
<h2>Passaggi successivi</h2><ol><li>Ampliare e motivare il campione.</li><li>Verificare documenti e coppie EU–AIC.</li><li>Acquisire anche testi, allegati, decorrenze e indicazioni rimborsate.</li><li>Preparare un benchmark di domande con evidenze ufficiali.</li></ol>
<p class="fine">Documento sperimentale. Non costituisce parere clinico o fonte ufficiale della classificazione vigente.</p>
</main></body></html>'''


def esporta(path=DEFAULT_DB, out=None):
    path=Path(path).resolve(); out=Path(out or path.parent).resolve()
    r=audit(path); rows=inventario(path)
    out.mkdir(parents=True,exist_ok=True)
    for name,data in [('qualita_database.json',json.dumps(r,indent=2,ensure_ascii=False).encode('utf8')),
                      ('relazione_primo_riscontro.md',relazione_md(r).encode('utf8')),
                      ('relazione_primo_riscontro.html',relazione_html(r).encode('utf8')),
                      ('inventario_medicinali.csv',csv_protetto(rows))]:
        (out/name).write_bytes(data)
    return r


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--out',type=Path)
    a=p.parse_args()
    try:
        r=esporta(a.db,a.out)
        t=r['tabelle']
        print(json.dumps({'esito':'OK','medicinali':t['ema_medicinali'],
            'confezioni':t['aifa_confezioni'],'link_documentati':t['link_verificati'],
            'candidati_da_verificare':t['match_candidati'],
            'atti_gazzetta':t['gazzetta_atti'],
            'report':str(Path(a.out or a.db.parent).resolve()/'relazione_primo_riscontro.md')},
            ensure_ascii=False,indent=2))
    except Exception as ex:
        p.exit(1,f'ERRORE AUDIT: {ex}\n')

if __name__=='__main__':
    main()
