#!/usr/bin/env python3
"""Etichette dei collegamenti AIFA-EMA per la consultazione, in sola lettura.

Le corrispondenze suggerite per nome NON diventano collegamenti verificati.
"""
from __future__ import annotations
from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3


def annota_collegamenti(db_path, confezioni):
    """Arricchisce fino a 200 confezioni con conteggi e stato documentale EMA."""
    if not confezioni:
        return []
    ids=list(dict.fromkeys(str(row['aic']) for row in confezioni))
    placeholders=','.join('?' for _ in ids)
    verified=defaultdict(set)
    candidates=defaultdict(set)
    uri=Path(db_path).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        for aic,ema_id in db.execute(
            f'SELECT aic,ema_id FROM link_verificati WHERE aic IN ({placeholders})',ids):
            verified[aic].add(ema_id)
        for aic,ema_id in db.execute(
            f'SELECT aic,ema_id FROM match_candidati WHERE aic IN ({placeholders})',ids):
            candidates[aic].add(ema_id)
    result=[]
    for item in confezioni:
        row=dict(item)
        aic=str(row['aic'])
        v=verified[aic]
        c=candidates[aic]
        row['stato_collegamento_ema']=(
            'Documentato (nel database)' if v
            else 'Candidato - da verificare' if c
            else 'Nessun collegamento documentato')
        row['ema_documentati']=len(v)
        row['ema_candidati']=len(c)
        row['codici_ema_documentati']=', '.join(sorted(v))
        result.append(row)
    return result
