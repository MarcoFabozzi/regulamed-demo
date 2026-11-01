#!/usr/bin/env python3
"""Aggiornatore separato EMA / AIFA / Gazzetta - RegulaMed.
Solo fonti pubbliche ufficiali. Nessuna promozione di match o classe corrente.
Esegue tutte le modifiche su una COPIA SQLite; l'app online legge l'ultimo snapshot verificato.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import urlparse

import pipeline
import aggiorna_automatico as etl

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / 'risultati' / 'farmaci_automatico.sqlite'
STATES = ROOT / 'risultati' / 'stato_fonti'
REQUIRED = {'ema_medicinali','aifa_confezioni','acquisizioni','link_verificati'}
MAX_AGE_LINKS_DAYS = 14


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def valid_state(path):
    try:
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        t = datetime.fromisoformat(d['ultimo_successo_utc'].replace('Z','+00:00'))
        return d if t.tzinfo and d.get('esito') == 'OK' else {}
    except (ValueError, KeyError, TypeError, OSError):
        return {}


def db_counts(db):
    return {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
            for name in ('ema_medicinali', 'aifa_confezioni','link_verificati',
                         'eventi_verificati','classi_snapshot','cnn_elenco_storico')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()}


def assure(db):
    tables = {t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED.issubset(tables):
        raise ValueError('Schema SQLite incompleto: ' + repr(REQUIRED-tables))
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Errore di integrita SQLite')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Violazione chiavi esterne')


def source_id(db, name, path, url, date_ref=None):
    return pipeline.registra_fonte(db, name, path, url=url,
                                  data_riferimento=date_ref,
                                  etichetta='FONTE_ISTITUZIONALE_DOWNLOAD_VERIFICATO')


def normalize(v):
    return pipeline.normalizza(v)


def update_ema(db, work):
    from catalogo_ema import importa_catalogo_ema
    src=etl.EMA_MEDICINES_JSON
    p=work/'ema.json'
    meta=etl.salva_dataset(src,p,etl.MAX_BYTES['ema'])
    rows=list(pipeline.lista_ema(p))
    if len(rows)<100:
        raise ValueError('Catalogo EMA troppo piccolo')
    sid=source_id(db,'ema',p,src)
    result=importa_catalogo_ema(db,rows,sid,target=300)
    return {**result,'fonti':{'ema':dict(url=src,**meta)}}


def discover_aifa():
    try:
        sources, date_ah = etl.scopri_fonti(True)
        return {k:v for k,v in sources.items() if k.startswith('aifa')}, date_ah, 'pagina_ufficiale'
    except Exception as exc:
        # Indice AIFA indisponibile: soltanto URL precedentemente verificati,
        # per massimo 14 giorni dall'ultima scoperta. I FILE vengono comunque riscaricati.
        manifest = ROOT / 'risultati' / 'rapporto_automatico.json'
        if not manifest.is_file():
            raise ValueError('Indice AIFA irraggiungibile e nessun URL precedente verificato') from exc
        prev = json.loads(manifest.read_text(encoding='utf-8'))
        if prev.get('modalita') != 'FONTI_UFFICIALI':
            raise ValueError('Provenienza del manifest AIFA non verificata') from exc
        raw_time = prev.get('ultima_scoperta_fonti_utc',prev['fine_utc'])
        t = datetime.fromisoformat(raw_time.replace('Z','+00:00'))
        if t.tzinfo is None or datetime.now(timezone.utc)-t > timedelta(days=MAX_AGE_LINKS_DAYS):
            raise ValueError('URL AIFA precedenti troppo vecchi: serve nuova scoperta') from exc
        keys = ('aifa','aifa_a','aifa_h','aifa_cnn')
        urls = {k:prev['fonti'][k]['url'] for k in keys}
        for u in urls.values():
            etl.assicurati_ufficiale(u)
        ah = {etl.data_liste_da_url(urls[k]) for k in ('aifa_a','aifa_h')}
        if len(ah)!=1 or None in ah:
            raise ValueError('Data liste A/H precedenti non verificabile')
        print('AVVISO: riuso temporaneo di URL ufficiali precedentemente verificati; i CSV saranno riscaricati.',flush=True)
        return urls, ah.pop(), 'url_precedenti_verificati'


def update_aifa(db, work):
    sources, date_ah, method = discover_aifa()
    names = {'aifa','aifa_a','aifa_h','aifa_cnn'}
    if set(sources)!=names:
        raise ValueError('Set AIFA incompleto')
    paths, metas = {}, {}
    for kind, url in sources.items():
        p = work / (kind + '.csv')
        metas[kind] = dict(url=url, **etl.salva_dataset(url,p,etl.MAX_BYTES[kind]))
        paths[kind] = p
    ps = list(pipeline.lista_aifa(paths['aifa']))
    if len(ps) < 1000:
        raise ValueError('Anagrafica AIFA troppo piccola')
    A,H = set(pipeline.lista_ah(paths['aifa_a'])),set(pipeline.lista_ah(paths['aifa_h']))
    if len(A)<1000 or len(H)<100 or A & H:
        raise ValueError('Liste A/H non valide o sovrapposte')
    cnn = list(pipeline.lista_cnn(paths['aifa_cnn']))
    if not cnn or not any(r['nome'] for r in cnn):
        raise ValueError('Elenco C(nn) non interpretabile')
    names_ema = {normalize(r[0]) for r in db.execute('SELECT nome FROM ema_medicinali')}
    verified_aics = {r[0] for r in db.execute('SELECT aic FROM link_verificati')}
    chosen = {p['aic']:p for p in ps if normalize(p['nome']) in names_ema or p['aic'] in verified_aics}
    old = {r[0]:r[1] for r in db.execute('SELECT aic,nome FROM aifa_confezioni')}
    if len(chosen)<max(10,len(old)//2) or not verified_aics.issubset(chosen):
        raise ValueError('Copertura AIFA insufficiente: non aggiorno il database')
    for aic,r in chosen.items():
        if aic in old and normalize(old[aic])!=normalize(r['nome']):
            raise ValueError('Denominazione AIC cambiata: '+aic)
    sid = source_id(db,'aifa',paths['aifa'],sources['aifa'])
    for aic,r in chosen.items():
        db.execute('''INSERT INTO aifa_confezioni VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(aic) DO UPDATE SET
            principio_attivo=excluded.principio_attivo,
            descrizione=excluded.descrizione,
            stato_amministrativo=excluded.stato_amministrativo,
            classe_da_anagrafica=excluded.classe_da_anagrafica,
            acquisizione_id=excluded.acquisizione_id''',
            (aic,r['nome'],r['principio_attivo'],r['descrizione'],
             r['stato_amministrativo'],r['classe_da_anagrafica'],sid))
    for cls,values in [('A',A),('H',H)]:
        key='aifa_'+cls.lower()
        aid=source_id(db,'classe_'+cls,paths[key],sources[key],date_ah)
        for aic in values.intersection(chosen):
            db.execute('INSERT OR IGNORE INTO classi_snapshot VALUES(?,?,?,?)',(aic,cls,date_ah,aid))
    sid_c=source_id(db,'aifa_cnn',paths['aifa_cnn'],sources['aifa_cnn'])
    selected_names={normalize(x['nome']) for x in chosen.values()}
    for r in cnn:
        if normalize(r['nome']) not in selected_names:
            continue
        rowid=hashlib.sha256(r['raw_json'].encode('utf8')).hexdigest()[:32]
        db.execute('INSERT OR IGNORE INTO cnn_elenco_storico VALUES(?,?,?,?,?,?,?,?,?)',
                   (rowid,r['nome'],r['numero_procedura'],r['principio_attivo'],r['data_chmp'],
                    r['data_decisione_ue'],r['riferimento_atto'],sid_c,r['raw_json']))
    # I match da verificare rimangono tali. Ricalcolo solo quelli per i record del campione.
    db.execute('DELETE FROM match_candidati')
    byname = {}
    for aic,r in chosen.items():
        byname.setdefault(normalize(r['nome']),[]).append((aic,r))
    for key,name,active in db.execute('SELECT ema_id,nome,principio_attivo FROM ema_medicinali'):
        for aic,p in byname.get(normalize(name),[]):
            if db.execute('SELECT 1 FROM link_verificati WHERE ema_id=? AND aic=?',(key,aic)).fetchone():
                continue
            match='NOME_ESATTO_E_PRINCIPIO_ATTIVO_COMPATIBILE' if active and p['principio_attivo'] and normalize(active)==normalize(p['principio_attivo']) else 'SOLO_NOME_ESATTO'
            db.execute('INSERT OR IGNORE INTO match_candidati VALUES (?,?,?,?)',(key,aic,match,'DA_VERIFICARE'))
    return {'file_confezioni':len(ps),'importate_campione':len(chosen),
            'data_riferimento_ah':date_ah,'metodo_scoperta':method,
            'fonti':metas, 'candidati':db.execute('SELECT COUNT(*) FROM match_candidati').fetchone()[0]}


def update_gu(db, work):
    # L'importatore originale viene eseguito sul database di staging:
    # no modifica a classi o collegamenti documentati.
    import gazzetta_incrementale
    return gazzetta_incrementale.run(db,work,limit_issues=30)


def run(source, db_path=DEFAULT_DB):
    db_path=Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    state_path=db_path.parent/'stato_fonti'/(source+'.json')
    state_path.parent.mkdir(parents=True,exist_ok=True)
    old=valid_state(state_path)
    with tempfile.TemporaryDirectory(prefix='.regulamed_'+source+'_',dir=db_path.parent) as t:
        work=Path(t)
        staged=work/'snapshot.sqlite'
        with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(staged)) as dst:
            src.backup(dst)
        with closing(sqlite3.connect(staged)) as con:
            con.execute('PRAGMA foreign_keys=ON')
            assure(con)
            before=db_counts(con)
            if source=='ema':
                info=update_ema(con,work)
            elif source=='aifa':
                info=update_aifa(con,work)
            elif source=='gazzetta':
                info=update_gu(con,work)
            else:
                raise ValueError('Fonte non autorizzata')
            con.commit()
            assure(con)
            after=db_counts(con)
            for name in ('link_verificati','eventi_verificati'):
                if after.get(name)!=before.get(name):
                    raise ValueError('Storico verificato o link documentali alterati: '+name)
        # GU import uses paths "gazzetta_testi/file.txt" relative to WORK.
        # Copy any new files before swapping the SQLite file. GitHub publishes
        # database and texts together in one verified commit.
        if source=='gazzetta':
            staged_txt=work/'gazzetta_testi'
            if staged_txt.exists():
                target=db_path.parent/'gazzetta_testi'
                target.mkdir(exist_ok=True)
                for file in staged_txt.glob('*.txt'):
                    shutil.copy2(file,target/file.name)
        os.replace(staged,db_path)
        record={'fonte':source,'esito':'OK','ultimo_successo_utc':now(),
                'precedente_successo_utc':old.get('ultimo_successo_utc'),
                'statistiche':{k:v for k,v in info.items() if k!='fonti'},
                'fonti':info.get('fonti',{}),
                'nota':'Controllo della fonte completato, non certificazione di stato regolatorio corrente'}
        state_tmp=work/(source+'.json')
        state_tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(state_tmp,state_path)
        return record


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--source',choices=['ema','aifa','gazzetta'],required=True)
    ap.add_argument('--db',type=Path,default=DEFAULT_DB)
    a=ap.parse_args()
    try:
        print(json.dumps(run(a.source,a.db),ensure_ascii=False,indent=2))
    except Exception as exc:
        ap.exit(1,f'AGGIORNAMENTO {a.source.upper()} FALLITO (snapshot precedente conservato): {exc}\n')
