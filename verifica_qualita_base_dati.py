#!/usr/bin/env python3
"""Controlli scientifici e strutturali FarmaQuery, SOLO LETTURA, senza rete.

Blocca la pubblicazione solo per errori verificabili; segnala a parte lacune
di copertura, fonti non aggiornate e casi che richiedono revisione umana.
Non certifica la classificazione corrente o l'efficacia di un provvedimento.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

DB_DEFAULT = Path(__file__).resolve().parent / 'risultati' / 'farmaci_automatico.sqlite'
REQUIRED = {'ema_medicinali','aifa_confezioni','link_verificati',
            'match_candidati','classi_snapshot','acquisizioni'}
SOURCES = ('ema','aifa_anagrafica','aifa_ah','aifa_cnn','gazzetta')
EMA_FIELDS = ('nome','principio_attivo','stato_ema','data_decisione_ue',
              'indicazione_ema','url_ema')
AIC_PATTERN = re.compile(r'0[0-9]{8}\Z')
EU_PATTERN = re.compile(r'EU/[0-9]/[0-9]{2}/[0-9]{4}/[0-9]{3}\Z',re.I)


def count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def json_sicuro(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError,UnicodeError):
        return None


def check_path(base, raw):
    if not raw:
        return None
    base = Path(base).resolve()
    p = Path(raw)
    resolved = (p if p.is_absolute() else base / p).resolve()
    return resolved if resolved.is_relative_to(base) and resolved.is_file() else None


def genera(db_path=DB_DEFAULT, min_ema=2000):
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    failed, warnings = [], []
    metrics = {}
    sources = {}
    uri = db_path.as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri,uri=True,timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            failed.append('PRAGMA integrity_check non superato')
        fk = len(db.execute('PRAGMA foreign_key_check').fetchall())
        metrics['errori_foreign_key'] = fk
        if fk:
            failed.append(f'{fk} riferimenti FK non validi')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED-tables
        if missing:
            failed.append('Tabelle richieste assenti: '+', '.join(sorted(missing)))
        if not missing and integrity == ['ok']:
            counts={table:count(db,f'SELECT COUNT(*) FROM {table}') for table in
                    sorted(tables.intersection(REQUIRED | {
                        'ema_revisioni_snapshot','gazzetta_atti','gu_allegati',
                        'gu_coppie_eu_aic','eventi_verificati','cnn_elenco_storico'}))}
            metrics['conteggi']=counts
            if counts['ema_medicinali']<min_ema:
                failed.append(f"Campione EMA sotto soglia: {counts['ema_medicinali']} < {min_ema}")
            if counts['link_verificati']<10:
                failed.append('Numero collegamenti documentati inferiore al minimo storico di 10')
            orphans={
                'link_senza_ema':count(db,'''SELECT COUNT(*) FROM link_verificati l
                    LEFT JOIN ema_medicinali e ON l.ema_id=e.ema_id WHERE e.ema_id IS NULL'''),
                'link_senza_aic':count(db,'''SELECT COUNT(*) FROM link_verificati l
                    LEFT JOIN aifa_confezioni a ON l.aic=a.aic WHERE a.aic IS NULL'''),
                'candidato_senza_ema':count(db,'''SELECT COUNT(*) FROM match_candidati m
                    LEFT JOIN ema_medicinali e ON m.ema_id=e.ema_id WHERE e.ema_id IS NULL'''),
                'candidato_senza_aic':count(db,'''SELECT COUNT(*) FROM match_candidati m
                    LEFT JOIN aifa_confezioni a ON m.aic=a.aic WHERE a.aic IS NULL'''),
                'snapshot_senza_aic':count(db,'''SELECT COUNT(*) FROM classi_snapshot s
                    LEFT JOIN aifa_confezioni a ON s.aic=a.aic WHERE a.aic IS NULL'''),
            }
            if 'eventi_verificati' in tables:
                orphans['evento_senza_ema']=count(db,'''SELECT COUNT(*) FROM eventi_verificati ev
                    LEFT JOIN ema_medicinali e ON ev.ema_id=e.ema_id WHERE e.ema_id IS NULL''')
            metrics['riferimenti_orfani']=orphans
            for label,n in orphans.items():
                if n:failed.append(f'{label}: {n}')
            metrics['aic_formato_invalido']=sum(
                1 for row in db.execute('SELECT aic FROM aifa_confezioni')
                if not AIC_PATTERN.fullmatch(row[0] or ''))
            if metrics['aic_formato_invalido']:
                failed.append('Codici AIC non conformi al formato a 9 cifre')
            invalid_hash = count(db,'''SELECT COUNT(*) FROM acquisizioni
                WHERE sha256 IS NULL OR LENGTH(sha256)!=64''')
            metrics['acquisizioni_senza_hash_valido']=invalid_hash
            if invalid_hash:failed.append('Acquisizioni con hash SHA-256 incompleto')
            conflict= count(db,'''SELECT COUNT(*) FROM (
                SELECT aic,data_riferimento FROM classi_snapshot
                GROUP BY aic,data_riferimento HAVING COUNT(DISTINCT classe)>1)''')
            metrics['conflitti_classi_stessa_data']=conflict
            if conflict:failed.append(f'Confezioni in A e H nella stessa data: {conflict}')
            dates_bad=0
            for r in db.execute('SELECT data_riferimento FROM classi_snapshot'):
                try:datetime.strptime(r[0],'%Y-%m-%d')
                except (ValueError,TypeError):dates_bad+=1
            metrics['date_snapshot_non_valide']=dates_bad
            if dates_bad:failed.append('Date snapshot A/H non interpretabili')
            if 'ema_revisioni_snapshot' in tables:
                history = {
                    r[0]:set() for r in db.execute('SELECT ema_id FROM ema_medicinali')}
                for r in db.execute('SELECT ema_id,sha256_riga FROM ema_revisioni_snapshot'):
                    history.setdefault(r[0],set()).add(r[1])
                inconsistencies=0
                no_history=0
                for row in db.execute('''SELECT ema_id,nome,principio_attivo,stato_ema,
                                  data_decisione_ue,indicazione_ema,url_ema FROM ema_medicinali'''):
                    p={k:row[k] for k in EMA_FIELDS}
                    raw=json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                    digest=sha256(raw.encode('utf-8')).hexdigest()
                    if not history.get(row['ema_id']):
                        no_history+=1
                    elif digest not in history[row['ema_id']]:
                        inconsistencies+=1
                metrics['medicinali_senza_storico_ema']=no_history
                metrics['stato_ema_non_nello_storico']=inconsistencies
                if no_history or inconsistencies:
                    failed.append('Lo storico EMA non rispecchia i record correnti')
            else:
                warnings.append('Tabella di versionamento EMA assente')
            metrics['gu_file_mancanti'] = 0
            if 'gazzetta_atti' in tables:
                for r in db.execute('SELECT testo_file FROM gazzetta_atti'):
                    if not check_path(db_path.parent,r[0]):
                        metrics['gu_file_mancanti']+=1
                if metrics['gu_file_mancanti']:
                    failed.append('Testi Gazzetta mancanti o fuori dalla cartella risultati')
            metrics['gu_allegati_mancanti'] = 0
            metrics['gu_allegati_hash_errato'] = 0
            metrics['gu_allegati_diversi_solo_per_fine_riga'] = 0
            if 'gu_allegati' in tables:
                for r in db.execute('SELECT testo_file,sha256 FROM gu_allegati'):
                    p=check_path(db_path.parent,r[0])
                    if not p:
                        metrics['gu_allegati_mancanti']+=1
                        continue
                    raw=p.read_bytes()
                    if sha256(raw).hexdigest()==r[1]:
                        continue
                    try:
                        # Il produttore GU registra SHA256 del testo UTF-8 normalizzato
                        # tramite Path.read_text / Path.write_text. Non e' l'hash raw
                        # dei byte se il checkout converte CRLF in LF o viceversa.
                        with p.open('r',encoding='utf-8',newline=None) as handle:
                            normalized=handle.read()
                        same=sha256(normalized.encode('utf-8')).hexdigest()==r[1]
                    except (OSError,UnicodeError):
                        same=False
                    if same:
                        metrics['gu_allegati_diversi_solo_per_fine_riga']+=1
                    else:
                        metrics['gu_allegati_hash_errato']+=1
                if metrics['gu_allegati_mancanti']:
                    failed.append('Allegati GU non accessibili')
                if metrics['gu_allegati_hash_errato']:
                    warnings.append('Hash del contenuto UTF-8 di alcuni allegati GU non coerente: revisione necessaria')
            if 'gu_coppie_eu_aic' in tables:
                metrics['coppie_gu_formato_invalido']=sum(
                    1 for r in db.execute('SELECT eu,aic FROM gu_coppie_eu_aic')
                    if not EU_PATTERN.fullmatch(r[0] or '') or
                    not AIC_PATTERN.fullmatch(r[1] or ''))
                if metrics['coppie_gu_formato_invalido']:
                    failed.append('Codici EU-AIC estratti con formato non valido')
            cov = count(db,'SELECT COUNT(DISTINCT ema_id) FROM link_verificati')
            metrics['medicinali_con_link_documentato']=cov
            metrics['percentuale_copertura_documentata']=round(100*cov/max(counts['ema_medicinali'],1),2)
            if counts['aifa_confezioni']<counts['ema_medicinali']:
                warnings.append('Copertura confezioni AIFA parziale rispetto al campione EMA: '
                    'è attesa durante l’indisponibilità delle fonti AIFA')
            if metrics['percentuale_copertura_documentata']<10:
                warnings.append('La percentuale di medicinali con link EU-AIC documentato è ancora bassa')
    for source in SOURCES:
        obj=json_sicuro(db_path.parent/'stato_fonti'/(source+'.json'))
        if not obj or obj.get('esito')!='OK' or obj.get('fonte')!=source:
            sources[source]={'stato':'NON_ANCORA_VERIFICATO','ultima_acquisizione_utc':None}
            warnings.append(f'{source}: non esiste ancora un rapporto di successo')
            continue
        try:
            t=datetime.fromisoformat(obj['ultimo_successo_utc'].replace('Z','+00:00'))
            if t.tzinfo is None:
                raise ValueError('timestamp senza fuso')
        except (KeyError,ValueError,TypeError):
            failed.append(f'{source}: data di successo non valida')
            continue
        sources[source]={'stato':'OK','ultima_acquisizione_utc':obj['ultimo_successo_utc']}
        if datetime.now(timezone.utc)-t.astimezone(timezone.utc)>timedelta(days=2):
            warnings.append(f'{source}: ultimo successo oltre 48 ore fa')
    return {
        'generato_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'stato':'KO' if failed else 'OK',
        'controlli_bloccanti':failed,'avvertenze':warnings,
        'metriche':metrics,'fonti':sources,
        'nota':'Solo qualita strutturale e provenienza locale: non verifica la classe vigente.'
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DB_DEFAULT)
    parser.add_argument('--min-ema',type=int,default=2000)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--strict',action='store_true')
    args=parser.parse_args()
    report=genera(args.db,args.min_ema)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'stato':report['stato'],
                      'controlli_bloccanti':report['controlli_bloccanti'],
                      'avvertenze':report['avvertenze'],
                      'metriche':report['metriche']},indent=2,ensure_ascii=False))
    if args.strict and report['stato']!='OK':
        raise SystemExit(2)


if __name__=='__main__':
    main()
