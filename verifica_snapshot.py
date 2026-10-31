#!/usr/bin/env python3
"""Guardia per la pubblicazione notturna. Non modifica i database."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import re
import sqlite3

REQUIRED = ('ema', 'aifa', 'aifa_a', 'aifa_h', 'aifa_cnn')
CRITICAL = ('link_verificati', 'eventi_verificati', 'gazzetta_atti',
            'gazzetta_evidenze_aic', 'gu_allegati', 'gu_coppie_eu_aic')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else {}


def db_data(path):
    with closing(sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro', uri=True)) as con:
        check = con.execute('PRAGMA integrity_check').fetchone()[0]
        if check != 'ok':
            raise ValueError(f'{path}: integrita SQLite fallita: {check}')
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError(f'{path}: foreign key non valide')
        tables = {r[0] for r in con.execute('SELECT name FROM sqlite_master WHERE type="table"')}
        if not {'ema_medicinali', 'aifa_confezioni', 'link_verificati', 'acquisizioni'} <= tables:
            raise ValueError(f'{path}: tabelle richieste assenti')
        counts = {t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                  for t in tables if t in (*CRITICAL, 'ema_medicinali', 'aifa_confezioni',
                                           'acquisizioni', 'classi_snapshot', 'cnn_elenco_storico')}
        rows = {t: con.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall()
                for t in CRITICAL if t in tables}
        return counts, rows


def verify(old_path, new_path, old_manifest, new_manifest):
    old_counts, old_rows = db_data(old_path)
    new_counts, new_rows = db_data(new_path)
    if new_counts['ema_medicinali'] < max(25, old_counts['ema_medicinali']):
        raise ValueError('Numero di medicinali EMA diminuito')
    if new_counts['aifa_confezioni'] < max(100, old_counts['aifa_confezioni']):
        raise ValueError('Numero di confezioni AIFA diminuito')
    for table in CRITICAL:
        if old_rows.get(table, []) != new_rows.get(table, []):
            raise ValueError(f'Dati documentali o collegamenti confermati cambiati senza revisione: {table}')
    for table in ('classi_snapshot', 'cnn_elenco_storico', 'acquisizioni'):
        if new_counts.get(table, 0) < old_counts.get(table, 0):
            raise ValueError(f'Storico ridotto: {table}')
    previous = read_json(old_manifest)
    latest = read_json(new_manifest)
    if latest.get('modalita') != 'FONTI_UFFICIALI':
        raise ValueError('Importazione non proveniente dalle fonti ufficiali')
    sources = latest.get('fonti', {})
    if not set(REQUIRED).issubset(sources):
        raise ValueError('Fonti EMA/AIFA A/H/C(nn) mancanti')
    for name in REQUIRED:
        info = sources[name]
        if not re.fullmatch(r'[0-9a-f]{64}', info.get('sha256', '')) or info.get('bytes', 0) < 30:
            raise ValueError(f'File non verificato: {name}')
    stats = latest.get('validazione', {})
    if stats.get('ema_estratti', 0) < 100 or stats.get('aifa_confezioni_estratte', 0) < 1000:
        raise ValueError('Anomalia nelle dimensioni dei dataset sorgente')
    current_date = latest.get('data_liste_ah')
    try:
        if not current_date:
            raise ValueError('Data lista A/H non disponibile')
        if previous.get('data_liste_ah') and date.fromisoformat(current_date) < date.fromisoformat(previous['data_liste_ah']):
            raise ValueError('Lista A/H piu vecchia della precedente')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Data A/H non valida: {exc}') from exc
    unchanged = all(previous.get('fonti', {}).get(n, {}).get('sha256') == sources[n]['sha256']
                    for n in REQUIRED) and previous.get('data_liste_ah') == current_date
    return {'esito': 'OK', 'fonti_immutate': unchanged, 'medicinali': new_counts['ema_medicinali'],
            'confezioni': new_counts['aifa_confezioni'],
            'link_documentati': new_counts['link_verificati']}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('vecchio_db')
    p.add_argument('nuovo_db')
    p.add_argument('vecchio_report')
    p.add_argument('nuovo_report')
    a = p.parse_args()
    result = verify(a.vecchio_db, a.nuovo_db, a.vecchio_report, a.nuovo_report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as f:
            f.write('changed=' + ('false' if result['fonti_immutate'] else 'true') + '\n')


if __name__ == '__main__':
    main()
