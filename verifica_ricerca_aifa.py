#!/usr/bin/env python3
"""Verifica locale dell'archivio AIFA completo contro il database principale."""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile

from aifa_archivio_completo import estrai_archivio

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'risultati'

def verifica(data_dir=DATA):
    data_dir=Path(data_dir)
    status=json.loads((data_dir/'stato_fonti'/'aifa_anagrafica.json').read_text(encoding='utf-8'))
    meta=status['statistiche']['archivio_completo']
    with tempfile.TemporaryDirectory() as tmp:
        src=Path(tmp)/'aifa.sqlite'
        extracted=estrai_archivio(data_dir/meta['file_archivio'],src,meta['sha256_archivio'])
        with closing(sqlite3.connect(src)) as all_db, closing(sqlite3.connect(data_dir/'farmaci_automatico.sqlite')) as main:
            all_db.execute('ATTACH DATABASE ? AS principale',(str((data_dir/'farmaci_automatico.sqlite').resolve()),))
            unmatched=all_db.execute("""SELECT COUNT(*) FROM principale.aifa_confezioni p
                LEFT JOIN main.confezioni a ON a.aic=p.aic WHERE a.aic IS NULL""").fetchone()[0]
            mismatched=all_db.execute("""SELECT COUNT(*) FROM principale.aifa_confezioni p
                JOIN main.confezioni a ON a.aic=p.aic
                WHERE UPPER(TRIM(a.nome))!=UPPER(TRIM(p.nome))""").fetchone()[0]
            documented=main.execute('SELECT COUNT(*) FROM link_verificati').fetchone()[0]
            counted=all_db.execute('SELECT COUNT(*) FROM confezioni').fetchone()[0]
    result={'confezioni_archivio':counted,'confezioni_report':meta['confezioni_totali'],
            'confezioni_principali_assenti_nell_archivio':unmatched,
            'denominazioni_diverse':mismatched,
            'link_documentati_preservati':documented,
            'esito':'OK' if counted==extracted==meta['confezioni_totali'] and unmatched==0
                     and mismatched==0 and documented>=10 else 'DA_VERIFICARE'}
    return result

if __name__=='__main__':
    report=verifica()
    print(json.dumps(report,indent=2,ensure_ascii=False))
    if report['esito']!='OK':
        raise SystemExit(2)
