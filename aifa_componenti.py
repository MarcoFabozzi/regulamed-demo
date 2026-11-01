#!/usr/bin/env python3
"""Tre componenti AIFA indipendenti, sempre su DB SQLite di staging.

* aifa_anagrafica: CSV confezioni (drive.aifa.gov.it) senza consultare indice AIFA
* aifa_ah: coppia A/H con identica data di riferimento, storicizzata
* aifa_cnn: elenco storico C(nn), mai prova di classe corrente

Non cancella link confermati, annotazioni, snapshot o dati di altre fonti.
"""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import aggiorna_automatico as etl
import pipeline

ROOT=Path(__file__).resolve().parent
LEGACY_REPORT=ROOT/'risultati'/'rapporto_automatico.json'
STATES=ROOT/'risultati'/'stato_fonti'
ANAGRAFICA='https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv'
MAX_FALLBACK_DAYS=14
GROUPS={'aifa_ah':('aifa_a','aifa_h'),'aifa_cnn':('aifa_cnn',)}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm(value):
    return pipeline.normalizza(value)


def assert_urls(urls,keys):
    if set(urls)!=set(keys):
        raise ValueError('Set di indirizzi incompleto')
    for kind,url in urls.items():
        etl.assicurati_ufficiale(url)
        filename=Path(urlparse(url).path).name.lower()
        if not filename.endswith('.csv'):
            raise ValueError('URL non CSV: '+kind)
        if kind=='aifa_a' and not ('classe-a' in filename or 'classe_a' in filename):
            raise ValueError('Nome file A non riconosciuto')
        if kind=='aifa_h' and not ('classe-h' in filename or 'classe_h' in filename):
            raise ValueError('Nome file H non riconosciuto')
        if kind=='aifa_cnn' and 'cnn' not in filename:
            raise ValueError('Nome file C(nn) non riconosciuto')


def riferimento_ah(urls):
    dates={etl.data_liste_da_url(urls[k]) for k in ('aifa_a','aifa_h')}
    if len(dates)!=1 or None in dates:
        raise ValueError('Elenchi A/H senza data di riferimento comune verificabile')
    return dates.pop()


def _fallback(group,reason):
    """Usa SOLO URL ufficiali gia registrati, con scadenza non prorogabile."""
    keys=GROUPS[group]
    state_file=STATES/(group+'.json')
    state=None
    if state_file.exists():
        state=json.loads(state_file.read_text(encoding='utf-8'))
        if state.get('esito')!='OK' or state.get('fonte')!=group:
            raise ValueError('Stato precedente non valido')
        all_urls={key:state['fonti'][key]['url'] for key in keys}
        previous_time=state.get('ultima_scoperta_indice_utc')
    else:
        if not LEGACY_REPORT.exists():
            raise RuntimeError('Nessun URL AIFA precedente attendibile') from reason
        manifest=json.loads(LEGACY_REPORT.read_text(encoding='utf-8'))
        if manifest.get('modalita')!='FONTI_UFFICIALI':
            raise RuntimeError('Provenienza dell ultimo report non verificata') from reason
        all_urls={key:manifest['fonti'][key]['url'] for key in keys}
        previous_time=manifest.get('ultima_scoperta_fonti_utc') or manifest['fine_utc']
    if not previous_time:
        raise ValueError('Ultima scoperta URL mancante') from reason
    ts=datetime.fromisoformat(previous_time.replace('Z','+00:00'))
    age=datetime.now(timezone.utc)-ts
    if ts.tzinfo is None or age<timedelta(days=0) or age>timedelta(days=MAX_FALLBACK_DAYS):
        raise RuntimeError('Gli URL AIFA noti sono scaduti: serve una nuova pagina indice') from reason
    assert_urls(all_urls,keys)
    if group=='aifa_ah':
        riferimento_ah(all_urls)
    print('AIFA '+group+': indice non disponibile, riuso temporaneo URL ufficiali '
          'già registrati; riscarico e valido i CSV.',flush=True)
    return all_urls,{'metodo_scoperta':'url_ufficiali_precedenti',
                     'ultima_scoperta_indice_utc':previous_time,
                     'nota_fallback':'La pagina AIFA non risponde; URL usati al massimo 14 giorni.'}


def scopri_liste(group):
    """Un guasto C(nn) non blocca A/H, e viceversa."""
    keys=GROUPS[group]
    try:
        with etl._request(etl.SOURCE_PAGE_AIFA) as res:
            body=res.read(3_000_001)
        if len(body)>3_000_000:
            raise ValueError('Pagina indice AIFA troppo grande')
        discovered=etl.pagine_fonti(body.decode('utf-8',errors='replace'))
        if not set(keys).issubset(discovered):
            raise ValueError('Il sito AIFA non pubblica tutti i link del gruppo '+group)
        urls={k:discovered[k] for k in keys}
        assert_urls(urls,keys)
        if group=='aifa_ah':
            riferimento_ah(urls)
        return urls,{'metodo_scoperta':'pagina_aifa',
                     'ultima_scoperta_indice_utc':utc()}
    except Exception as exc:
        return _fallback(group,exc)


def aggiorna_anagrafica(db,work):
    """Aggiorna tutte le confezioni rilevanti per gli EMA attualmente archiviati."""
    path=Path(work)/'aifa_anagrafica.csv'
    meta=etl.salva_dataset(ANAGRAFICA,path,etl.MAX_BYTES['aifa'])
    source_rows=list(pipeline.lista_aifa(path))
    if len(source_rows)<1000:
        raise ValueError('Dataset anagrafica AIFA troppo piccolo o danneggiato')
    seen={}
    for record in source_rows:
        aic=record['aic']
        if aic in seen and record!=seen[aic]:
            raise ValueError('AIC duplicata con dati discordanti: '+aic)
        seen[aic]=record
    ema_names={norm(row[0]) for row in db.execute('SELECT nome FROM ema_medicinali')}
    old={r[0]:r[1] for r in db.execute('SELECT aic,nome FROM aifa_confezioni')}
    checked={r[0] for r in db.execute('SELECT DISTINCT aic FROM link_verificati')}
    chosen={aic:record for aic,record in seen.items()
            if norm(record['nome']) in ema_names or aic in old or aic in checked}
    if len(chosen)<max(100,int(len(old)*0.8)):
        raise ValueError('Copertura del campione AIFA insufficiente rispetto al database precedente')
    for aic,record in chosen.items():
        if aic in old and norm(old[aic])!=norm(record['nome']):
            raise ValueError('Denominazione AIC cambiata: '+aic+'; conservato database precedente')
    from aifa_archivio_completo import crea_archivio, ARCHIVE_NAME
    archive=crea_archivio(seen.values(),Path(work)/ARCHIVE_NAME,meta['sha256'],ANAGRAFICA)
    sid=pipeline.registra_fonte(db,'aifa',path,ANAGRAFICA,
        etichetta='FONTE_ISTITUZIONALE_ANAGRAFICA_NON_PROVA_CLASSE_VIGENTE')
    fresh=0
    for aic,record in chosen.items():
        if aic not in old:fresh+=1
        db.execute("""INSERT INTO aifa_confezioni
           (aic,nome,principio_attivo,descrizione,stato_amministrativo,
            classe_da_anagrafica,acquisizione_id) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(aic) DO UPDATE SET
            principio_attivo=excluded.principio_attivo,
            descrizione=excluded.descrizione,
            stato_amministrativo=excluded.stato_amministrativo,
            classe_da_anagrafica=excluded.classe_da_anagrafica,
            acquisizione_id=excluded.acquisizione_id""",
            (aic,record['nome'],record['principio_attivo'],record['descrizione'],
             record['stato_amministrativo'],record['classe_da_anagrafica'],sid))
    # Candidature incrementali: non cancellare le annotazioni già revisionate.
    by_name={}
    for aic,record in chosen.items():
        by_name.setdefault(norm(record['nome']),[]).append((aic,record))
    inserted=0
    for ema_id,nome,active in db.execute('SELECT ema_id,nome,principio_attivo FROM ema_medicinali'):
        for aic,record in by_name.get(norm(nome),[]):
            if db.execute('SELECT 1 FROM link_verificati WHERE ema_id=? AND aic=?',(ema_id,aic)).fetchone():
                continue
            criterion=('NOME_ESATTO_E_PRINCIPIO_ATTIVO_COMPATIBILE'
              if norm(active) and norm(active)==norm(record['principio_attivo'])
              else 'SOLO_NOME_ESATTO')
            count=db.execute("""INSERT OR IGNORE INTO match_candidati
                 (ema_id,aic,criterio,stato) VALUES (?,?,?,'DA_VERIFICARE')""",
                 (ema_id,aic,criterion)).rowcount
            inserted+=count
    return {'confezioni_file':len(seen),'archivio_completo':archive,'confezioni_pertinenti':len(chosen),
            'nuove_confezioni':fresh,'nuovi_match_candidati':inserted,
            'aic_precedenti_non_nel_file':len(set(old)-set(seen)),
            'nota':'L anagrafica non prova la classe vigente; le AIC assenti restano storiche',
            'fonti':{'aifa':dict(url=ANAGRAFICA,**meta)}}


def aggiorna_ah(db,work):
    sources,discovery=scopri_liste('aifa_ah')
    date_ref=riferimento_ah(sources)
    prior=db.execute('SELECT MAX(data_riferimento) FROM classi_snapshot').fetchone()[0]
    if prior and date_ref<prior:
        raise ValueError('Elenchi A/H precedenti alla versione più recente conservata')
    paths={}
    meta={}
    for key in ('aifa_a','aifa_h'):
        p=Path(work)/(key+'.csv')
        meta[key]=dict(url=sources[key],**etl.salva_dataset(sources[key],p,etl.MAX_BYTES[key]))
        paths[key]=p
    classes={cls:set(pipeline.lista_ah(paths[key]))
             for cls,key in (('A','aifa_a'),('H','aifa_h'))}
    if len(classes['A'])<1000 or len(classes['H'])<100 or classes['A'] & classes['H']:
        raise ValueError('Liste A/H incoerenti o troppo piccole')
    known={row[0] for row in db.execute('SELECT aic FROM aifa_confezioni')}
    inserted=0
    for cls,key in (('A','aifa_a'),('H','aifa_h')):
        sid=pipeline.registra_fonte(db,'classe_'+cls,paths[key],sources[key],
             data_riferimento=date_ref,
             etichetta='LISTA_AIFA_AH_SNAPSHOT_NON_CLASSIFICAZIONE_VIGENTE')
        for aic in classes[cls] & known:
            inserted+=db.execute("""INSERT OR IGNORE INTO classi_snapshot
                                    (aic,classe,data_riferimento,acquisizione_id)
                                    VALUES (?,?,?,?)""",(aic,cls,date_ref,sid)).rowcount
    return {'data_riferimento_ah':date_ref,'a_file':len(classes['A']),
            'h_file':len(classes['H']),'nuove_righe_snapshot':inserted,
            'confezioni_abbinate':sum(len(classes[x]&known) for x in ('A','H')),
            'fonti':meta,**discovery}


def _date_cnn_from_url(url):
    import re
    fn=Path(urlparse(url).path).name
    m=re.search(r'(\d{1,2})[._-](\d{1,2})[._-](20\d{2})',fn)
    if not m:return None
    try:return datetime(int(m[3]),int(m[2]),int(m[1])).date().isoformat()
    except ValueError:return None


def aggiorna_cnn(db,work):
    sources,discovery=scopri_liste('aifa_cnn')
    url=sources['aifa_cnn']
    date_ref=_date_cnn_from_url(url)
    p=Path(work)/'aifa_cnn.csv'
    meta=etl.salva_dataset(url,p,etl.MAX_BYTES['aifa_cnn'])
    records=list(pipeline.lista_cnn(p))
    if len(records)<10:
        raise ValueError('Elenco C(nn) troppo piccolo o non interpretabile')
    ema_names={norm(row[0]) for row in db.execute('SELECT nome FROM ema_medicinali')}
    selected=[r for r in records if norm(r['nome']) in ema_names]
    sid=pipeline.registra_fonte(db,'aifa_cnn',p,url,
        data_riferimento=date_ref,
        etichetta='ELENCO_STORICO_CNN_NON_CLASSIFICAZIONE_ATTUALE')
    added=0
    for r in selected:
        ident=hashlib.sha256(r['raw_json'].encode('utf-8')).hexdigest()[:32]
        added+=db.execute("""INSERT OR IGNORE INTO cnn_elenco_storico
            (row_id,nome,numero_procedura,principio_attivo,data_chmp,
             data_decisione_ue,riferimento_atto,acquisizione_id,raw_json)
             VALUES (?,?,?,?,?,?,?,?,?)""",
             (ident,r['nome'],r['numero_procedura'],r['principio_attivo'],
              r['data_chmp'],r['data_decisione_ue'],r['riferimento_atto'],
              sid,r['raw_json'])).rowcount
    return {'data_riferimento_file_cnn':date_ref,'righe_file_cnn':len(records),
            'righe_pertinenti':len(selected),'nuove_righe_storiche':added,
            'nota':'La C(nn) in elenco storico non certifica la classificazione attuale',
            'fonti':{'aifa_cnn':dict(url=url,**meta)},**discovery}


def aggiorna(group,db,work):
    funcs={'aifa_anagrafica':aggiorna_anagrafica,
           'aifa_ah':aggiorna_ah,'aifa_cnn':aggiorna_cnn}
    if group not in funcs:
        raise ValueError('Componente AIFA sconosciuto')
    return funcs[group](db,work)
