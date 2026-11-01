#!/usr/bin/env python3
"""Ampliamento graduale EMA e storico delle versioni osservate.
Le versioni osservate NON equivalgono a nuove autorizzazioni o atti normativi.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import re

FIELDS=('nome','principio_attivo','stato_ema','data_decisione_ue','indicazione_ema','url_ema')
SCHEMA="""
CREATE TABLE IF NOT EXISTS ema_revisioni_snapshot(
 ema_id TEXT NOT NULL REFERENCES ema_medicinali(ema_id),
 sha256_riga TEXT NOT NULL,
 prima_osservazione_utc TEXT NOT NULL,
 ultima_osservazione_utc TEXT NOT NULL,
 acquisizione_id TEXT NOT NULL REFERENCES acquisizioni(id),
 valori_json TEXT NOT NULL,
 PRIMARY KEY(ema_id,sha256_riga));
CREATE INDEX IF NOT EXISTS idx_ema_revisioni_data
 ON ema_revisioni_snapshot(ema_id,prima_osservazione_utc);
"""

def norm(v):
    return re.sub(r'[^A-Z0-9]+','',str(v or '').upper())

def payload(r):
    return {k:r.get(k) for k in FIELDS}

def digest(values):
    raw=json.dumps(values,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest(),raw

def osserva(db,ema_id,values,when,acquisition):
    h,raw=digest(values)
    db.execute("""INSERT INTO ema_revisioni_snapshot
     (ema_id,sha256_riga,prima_osservazione_utc,ultima_osservazione_utc,acquisizione_id,valori_json)
     VALUES (?,?,?,?,?,?)
     ON CONFLICT(ema_id,sha256_riga)
     DO UPDATE SET ultima_osservazione_utc=MAX(ema_revisioni_snapshot.ultima_osservazione_utc,excluded.ultima_osservazione_utc)""",
     (ema_id,h,when,when,acquisition,raw))

def importa_catalogo_ema(db,rows,sid,target=300,stamp=None):
    """Estende senza rimuovere gli ID storici o creare nuovi match verificati."""
    if target<30:
        raise ValueError('Target inferiore al campione minimo (30)')
    stamp=stamp or datetime.now(timezone.utc).isoformat(timespec='seconds')
    db.executescript(SCHEMA)
    fields=('ema_id',)+FIELDS+('acquisizione_id',)
    existing={r[0]:dict(zip(fields,r)) for r in db.execute(
        """SELECT ema_id,nome,principio_attivo,stato_ema,data_decisione_ue,
                  indicazione_ema,url_ema,acquisizione_id FROM ema_medicinali""")}
    if len(existing)<20:
        raise ValueError('Campione esistente troppo piccolo: blocco preventivo')
    catalog={}
    for item in rows:
        ident=item['ema_id']
        if ident in catalog and payload(catalog[ident])!=payload(item):
            raise ValueError('Record EMA duplicato con valori discordanti: '+ident)
        catalog[ident]=item
    if len(catalog)<max(100,len(existing)):
        raise ValueError('Catalogo EMA troppo piccolo: non aggiornare lo snapshot')
    missing=set(existing)-set(catalog)
    if missing:
        raise ValueError(f'{len(missing)} medicinali preesistenti mancanti nel dataset')
    for ident,prior in existing.items():
        if norm(prior['nome'])!=norm(catalog[ident]['nome']):
            raise ValueError('Denominazione EMA cambiata per '+ident)
    aifa_names={norm(r[0]) for r in db.execute('SELECT DISTINCT nome FROM aifa_confezioni')}
    candidates=sorted((r for k,r in catalog.items() if k not in existing),
        key=lambda r:(0 if norm(r['nome']) in aifa_names else 1,norm(r['nome']),r['ema_id']))
    selected=[catalog[k] for k in sorted(existing)]
    selected.extend(candidates[:max(0,max(target,len(existing))-len(existing))])
    added=0
    for r in selected:
        ident=r['ema_id']
        if ident in existing:
            old=existing[ident]
            acq=db.execute('SELECT data_acquisizione FROM acquisizioni WHERE id=?',
                           (old['acquisizione_id'],)).fetchone()
            old_when=acq[0] if acq and acq[0] else stamp
            osserva(db,ident,payload(old),old_when,old['acquisizione_id'])
        else:
            added+=1
        db.execute("""INSERT INTO ema_medicinali
          (ema_id,nome,principio_attivo,stato_ema,data_decisione_ue,
           indicazione_ema,url_ema,acquisizione_id) VALUES (?,?,?,?,?,?,?,?)
          ON CONFLICT(ema_id) DO UPDATE SET
           nome=excluded.nome,principio_attivo=excluded.principio_attivo,
           stato_ema=excluded.stato_ema,data_decisione_ue=excluded.data_decisione_ue,
           indicazione_ema=excluded.indicazione_ema,url_ema=excluded.url_ema,
           acquisizione_id=excluded.acquisizione_id""",
          (ident,*(r.get(k) for k in FIELDS),sid))
        osserva(db,ident,payload(r),stamp,sid)
    total=db.execute('SELECT COUNT(*) FROM ema_medicinali').fetchone()[0]
    revisions=db.execute('SELECT COUNT(*) FROM ema_revisioni_snapshot').fetchone()[0]
    return {'catalogo_sorgente':len(catalog),'target':target,'nuovi_medicinali':added,
            'medicinali_archiviati':total,'versioni_ema_osservate':revisions,
            'nota':'Versioni osservate nel dataset, non eventi normativi o date di prima autorizzazione'}
