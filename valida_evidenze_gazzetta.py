#!/usr/bin/env python3
"""Audit documentale in sola lettura delle coppie EU-AIC estratte dalla Gazzetta.

Controlla: tracciabilita dell'allegato, integrita del file, vicinanza dei codici
nel testo, possibili conflitti con i link gia documentati. Non conferma
automaticamente la corrispondenza normativa ne modifica il database.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from gazzetta_incrementale import coppie_eu_aic_da_testo

DEFAULT_DB=Path(__file__).resolve().parent/'risultati'/'farmaci_automatico.sqlite'


def file_sicuro(db_dir, filename):
    if not filename or not isinstance(filename,str):
        raise ValueError('Percorso allegato mancante')
    base=Path(db_dir).resolve()
    path=Path(filename)
    candidate=(path if path.is_absolute() else base/path).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError('Percorso allegato esterno al database')
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def esamina(db_path):
    db_path=Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    rows=[]
    documents={}
    with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('SQLite non integro')
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Riferimenti SQLite non validi')
        for table in ('gu_coppie_eu_aic','gu_allegati','gazzetta_atti','link_verificati'):
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                raise ValueError('Tabella assente: '+table)
        sql="""SELECT p.codice,p.eu,p.aic,p.stato,p.url_allegato,
                      a.testo_file,a.sha256,g.url_ufficiale,g.data_pubblicazione
                 FROM gu_coppie_eu_aic p
                 LEFT JOIN gu_allegati a ON a.codice=p.codice AND a.url=p.url_allegato
                 LEFT JOIN gazzetta_atti g ON g.codice=p.codice
                 ORDER BY p.codice,p.eu,p.aic"""
        for record in con.execute(sql).fetchall():
            r=dict(record)
            doc_key=(r['codice'],r['url_allegato'])
            reasons=[]
            pairs={}
            if doc_key not in documents:
                try:
                    p=file_sicuro(db_path.parent,r.get('testo_file'))
                    text=p.read_text(encoding='utf-8')
                    digest=sha256(text.encode('utf-8')).hexdigest()
                    is_intact=(digest == r.get('sha256'))
                    documents[doc_key]={
                        'sha256_corrispondente':is_intact,
                        'coppie':coppie_eu_aic_da_testo(text),
                        'testo_file':str(Path(r['testo_file'])),
                    }
                except (OSError,ValueError,TypeError,UnicodeError) as exc:
                    documents[doc_key]={'errore':str(exc),'coppie':{},'sha256_corrispondente':False}
            doc=documents[doc_key]
            if not r.get('url_ufficiale'):
                reasons.append('atto_senza_url')
            if 'errore' in doc:
                reasons.append('testo_allegato_non_disponibile')
            if not doc.get('sha256_corrispondente'):
                reasons.append('sha256_allegato_non_coerente')
            pair=(r['eu'].upper(),r['aic'])
            if pair not in doc['coppie']:
                reasons.append('coppia_non_riproducibile_nel_testo')
            others=sorted({aic for eu,aic in doc['coppie'] if eu==pair[0] and aic!=pair[1]})
            if others:
                reasons.append('presentazioni_ue_con_piu_aic_nel_testo')
            linked=con.execute('''SELECT ema_id,aic,codice_presentazione_ue
                                 FROM link_verificati
                                 WHERE codice_presentazione_ue=? OR aic=?''',
                               (pair[0],pair[1])).fetchall()
            contradict=[dict(x) for x in linked
                        if (x['codice_presentazione_ue'] and
                            x['codice_presentazione_ue'].upper()==pair[0] and
                            x['aic']!=pair[1]) or
                           (x['aic']==pair[1] and x['codice_presentazione_ue'] and
                            x['codice_presentazione_ue'].upper()!=pair[0])]
            if contradict:
                reasons.append('possibile_discrepanza_con_link_documentato')
            structural={'atto_senza_url','testo_allegato_non_disponibile',
                        'sha256_allegato_non_coerente','coppia_non_riproducibile_nel_testo'}
            state=('ANOMALIA_DOCUMENTALE' if structural.intersection(reasons)
                   else 'REVISIONE_PRIORITARIA' if reasons
                   else 'COERENTE_NEL_TESTO_DA_REVISIONARE')
            rows.append({
                'atto':r['codice'],'data_pubblicazione_atto':r['data_pubblicazione'],
                'presentazione_ue':pair[0],'aic':pair[1],
                'esito_controllo_automatico':state,'motivi':reasons,
                'fonte_ufficiale':r['url_ufficiale'],
                'url_allegato':r['url_allegato'],
                'estratto_documentale':doc['coppie'].get(pair,'')[:240],
                'stato_nel_database':r['stato'],
                'conflitti_con_link_esistenti':contradict
            })
        counts={status:sum(r['esito_controllo_automatico']==status for r in rows)
                for status in ('COERENTE_NEL_TESTO_DA_REVISIONARE',
                               'REVISIONE_PRIORITARIA','ANOMALIA_DOCUMENTALE')}
        by_act={}
        for r in rows:
            by_act.setdefault(r['atto'],{'totale':0,'coerenti_nel_testo':0})
            by_act[r['atto']]['totale']+=1
            by_act[r['atto']]['coerenti_nel_testo']+=int(r['esito_controllo_automatico']=='COERENTE_NEL_TESTO_DA_REVISIONARE')
        return {'generato_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'criterio':'Ricontrollo automatico per prossimita EU/AIC, hash e collegamenti. Non certifica autorizzazione, classificazione vigente o rimborsabilita.',
                'totale_coppie':len(rows),'riepilogo':counts,'per_atto':by_act,
                'coppie':rows}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--output',type=Path)
    p.add_argument('--strict',action='store_true',help='Fallisce se ci sono anomalie strutturali')
    args=p.parse_args()
    result=esamina(args.db)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        tmp=args.output.with_suffix(args.output.suffix+'.part')
        try:
            tmp.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
            tmp.replace(args.output)
        finally:
            tmp.unlink(missing_ok=True)
    print(json.dumps({'coppie':result['totale_coppie'],'esiti':result['riepilogo'],
                      'per_atto':result['per_atto']},ensure_ascii=False,indent=2))
    if args.strict and result['riepilogo']['ANOMALIA_DOCUMENTALE']:
        raise SystemExit(2)


if __name__=='__main__':
    main()
