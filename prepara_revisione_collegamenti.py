#!/usr/bin/env python3
"""Elenco di revisione EU-AIC da fonti ufficiali, senza promuovere link.

Incrocia le coppie estratte dalla GU con le anagrafiche AIFA e EMA;
match nominali = candidati, non prove autorizzative.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from pipeline import normalizza

DEFAULT=Path(__file__).resolve().parent/'risultati'/'farmaci_automatico.sqlite'

def prepara(db_path=DEFAULT):
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        pairs=db.execute("""SELECT p.codice,p.eu,p.aic,p.stato,g.data_pubblicazione,
                    g.url_ufficiale,a.nome AS nome_aifa,a.principio_attivo AS attivo_aifa,
                    a.descrizione, e.ema_id AS ema_documentato,e.nome AS nome_ema_documentato
                    FROM gu_coppie_eu_aic p
                    LEFT JOIN gazzetta_atti g ON g.codice=p.codice
                    LEFT JOIN aifa_confezioni a ON a.aic=p.aic
                    LEFT JOIN link_verificati l ON l.aic=p.aic
                         AND UPPER(l.codice_presentazione_ue)=UPPER(p.eu)
                    LEFT JOIN ema_medicinali e ON e.ema_id=l.ema_id
                    ORDER BY p.codice,p.eu,p.aic""").fetchall()
        ema_names={}
        for r in db.execute('SELECT ema_id,nome,principio_attivo FROM ema_medicinali'):
            ema_names.setdefault(normalizza(r['nome']),[]).append((r['ema_id'],r['nome'],r['principio_attivo']))
        output=[]
        for r in pairs:
            r=dict(r)
            names=ema_names.get(normalizza(r['nome_aifa']),[]) if r['nome_aifa'] else []
            if r['ema_documentato']:
                state='COLLEGAMENTO_GIA_DOCUMENTATO'
            elif not r['nome_aifa']:
                state='AIC_NON_NEL_DATABASE_PRINCIPALE'
            elif len(names)==1:
                state='CANDIDATO_NOME_EMA_DA_REVISIONARE'
            elif len(names)>1:
                state='NOME_EMA_AMBIGUO_DA_REVISIONARE'
            else:
                state='NESSUNA_CORRISPONDENZA_NOMINALE_EMA'
            output.append({
                'atto_gu':r['codice'],'data_pubblicazione':r['data_pubblicazione'],
                'fonte_ufficiale':r['url_ufficiale'],
                'codice_presentazione_ue':r['eu'],'aic':r['aic'],
                'nome_aifa':r['nome_aifa'],'principio_attivo_aifa':r['attivo_aifa'],
                'ema_documentato':r['ema_documentato'],
                'stato_revisione':state,
                'candidati_ema_per_nome':[{'ema_id':i,'nome':n,'principio_attivo':a}
                                           for i,n,a in names],
                'avvertenza':'L abbinamento per nome non conferma la presentazione UE o lo stato regolatorio.'
            })
        states=sorted({r['stato_revisione'] for r in output})
        return {'generato_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'totale_evidenze':len(output),
                'riepilogo':{k:sum(r['stato_revisione']==k for r in output) for k in states},
                'nota':'Solo confronto documentale. Nessuna modifica al database e nessun link verificato creato.',
                'elementi':output}

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=Path,default=DEFAULT)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=prepara(args.db)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'totale_evidenze':result['totale_evidenze'],'riepilogo':result['riepilogo']},ensure_ascii=False))
