#!/usr/bin/env python3
"""Aggiornamento unattended EMA–AIFA: discovery link versionati, download, validazione,
importazione su copia temporanea del DB e promozione atomica se TUTTI i passi riescono.

Esecuzione reale richiede accesso Internet; nessun LLM interviene nell'ingestione.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import pipeline

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / 'risultati' / 'farmaci_automatico.sqlite'
SOURCE_PAGE_AIFA = 'https://www.aifa.gov.it/it/liste-dei-farmaci'
EMA_MEDICINES_JSON = 'https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json'
ALLOWED_HOSTS = {'www.ema.europa.eu', 'www.aifa.gov.it', 'drive.aifa.gov.it'}
MAX_BYTES = {'ema': 120_000_000, 'aifa': 180_000_000, 'aifa_a': 20_000_000,
             'aifa_h': 20_000_000, 'aifa_cnn': 80_000_000}


class LinkReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'a':
            return
        href = dict(attrs).get('href')
        if href:
            self.links.append(href)


def assicurati_ufficiale(url: str):
    p = urlparse(url)
    if p.scheme != 'https' or (p.hostname or '').lower() not in ALLOWED_HOSTS:
        raise ValueError(f'URL non ufficiale o protocollo non HTTPS: {url}')
    if p.username or p.password:
        raise ValueError('URL con credenziali non ammesso')
    return url


def _request(url: str, timeout: float = 45):
    assicurati_ufficiale(url)
    req = Request(url, headers={
        'User-Agent': 'TesiFarmaciETL/1.0 (academic research, public regulatory data)',
        'Accept': 'application/json,text/csv,text/html,application/octet-stream,*/*'
    })
    response = urlopen(req, timeout=timeout)
    try:
        assicurati_ufficiale(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def pagine_fonti(page_html: str, base_url: str = SOURCE_PAGE_AIFA):
    """Rileva i link CSV della pagina AIFA; NON inventa le URL dei file datati."""
    p = LinkReader()
    p.feed(page_html)
    found: dict[str, str] = {}
    for href in p.links:
        url = urljoin(base_url, href)
        lower = urlparse(url).path.lower()
        if not lower.endswith('.csv'):
            continue
        try:
            assicurati_ufficiale(url)
        except ValueError:
            continue
        name = Path(lower).name
        if name == 'confezioni_fornitura.csv':
            found['aifa'] = url
        elif re.search(r'classe[-_]a.*nome[-_]commerciale', name):
            found['aifa_a'] = url
        elif re.search(r'classe[-_]h.*nome[-_]commerciale', name):
            found['aifa_h'] = url
        elif ('cnn' in name and ('farmaci' in name or 'classe' in name)):
            found['aifa_cnn'] = url
    return found


def data_liste_da_url(url: str):
    """Data di riferimento delle liste A/H dal nome file, mai dal giorno di download."""
    fn = Path(urlparse(url).path).name
    m = re.search(r'(?:_|-)(\d{1,2})[-_.](\d{1,2})[-_.](20\d\d)(?:\D|$)', fn)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
        except ValueError:
            return None
    return None


def scopri_fonti(include_lists=True, reader=None):
    reader = reader or _request
    with reader(SOURCE_PAGE_AIFA) as r:
        payload = r.read(3_000_000 + 1)
    if len(payload) > 3_000_000:
        raise ValueError('Pagina AIFA troppo grande')
    html = payload.decode('utf-8', errors='replace')
    result = {'ema': EMA_MEDICINES_JSON, **pagine_fonti(html)}
    required = {'ema', 'aifa'}
    if include_lists:
        required |= {'aifa_a', 'aifa_h', 'aifa_cnn'}
    missing = required - set(result)
    if missing:
        raise ValueError(f'Link richiesti non rilevati sulla pagina ufficiale AIFA: {sorted(missing)}. Non uso URL datati inventati.')
    result = {k: result[k] for k in ('ema', 'aifa', 'aifa_a', 'aifa_h', 'aifa_cnn') if k in required}
    data_liste = None
    if include_lists:
        date = {data_liste_da_url(result[k]) for k in ('aifa_a', 'aifa_h')}
        if len(date) != 1 or None in date:
            raise ValueError('Impossibile determinare una data A/H comune e non ambigua dai file ufficiali')
        data_liste = date.pop()
    return result, data_liste


def salva_dataset(url, dest: Path, max_size: int, reader=None):
    reader = reader or _request
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Il file entra nella cartella RAW soltanto dopo aver verificato tipo e dimensione.
    tmp = dest.with_name(dest.name + '.part')
    try:
        sha = hashlib.sha256()
        n = 0
        with reader(url) as resp, tmp.open('wb') as output:
            while True:
                block = resp.read(512 * 1024)
                if not block:
                    break
                n += len(block)
                if n > max_size:
                    raise ValueError(f'File troppo grande (> {max_size} byte)')
                sha.update(block)
                output.write(block)
        if n < 30:
            raise ValueError('File vuoto o eccessivamente piccolo')
        with tmp.open('rb') as f:
            head = f.read(256).lstrip().lower()
        if head.startswith((b'<html', b'<!doctype', b'<?xml')):
            raise ValueError('Il server ha restituito HTML/XML invece dei dati')
        os.replace(tmp, dest)
        return {'bytes': n, 'sha256': sha.hexdigest()}
    finally:
        tmp.unlink(missing_ok=True)


def valida_file(paths, min_ema=30, min_aifa=100):
    es = list(pipeline.lista_ema(paths['ema']))
    ps = list(pipeline.lista_aifa(paths['aifa']))
    if len(es) < min_ema or len(ps) < min_aifa:
        raise ValueError(f'Formato cambiato o dataset anomalo: EMA={len(es)}, AIFA={len(ps)}; soglie {min_ema}/{min_aifa}. DB precedente conservato.')
    result = {'ema_estratti': len(es), 'aifa_confezioni_estratte': len(ps)}
    seen_classes = {}
    for kind in ('aifa_a', 'aifa_h'):
        if kind in paths:
            rows = set(pipeline.lista_ah(paths[kind]))
            if not rows:
                raise ValueError(f'{kind}: elenco AIC vuoto/non riconosciuto')
            result[kind + '_aic'] = len(rows)
            seen_classes[kind] = rows
    if 'aifa_a' in seen_classes and 'aifa_h' in seen_classes:
        intersection = seen_classes['aifa_a'] & seen_classes['aifa_h']
        if intersection:
            raise ValueError(f'Conflitto fra le liste A e H su {len(intersection)} codici AIC; importazione interrotta')
    if 'aifa_cnn' in paths:
        cnn = list(pipeline.lista_cnn(paths['aifa_cnn']))
        if not cnn or not any(x['nome'] for x in cnn):
            raise ValueError('aifa_cnn: elenco C(nn) vuoto/non riconosciuto')
        result['aifa_cnn_righe'] = len(cnn)
    return result


def aggiorna(dest_db=DEFAULT_DB, limite=30, include_lists=True, min_ema=30,
             min_aifa=100, reader=None, sources=None, data_liste=None,
             pilota=True, report_path=None):
    """Operazione transazionale: i file e il database pubblicato non si modificano
    se il download, la lettura, l'import o i controlli falliscono.
    `sources` permette di testare usando server simulati, NON di falsificare fonti.
    """
    dest_db = Path(dest_db).resolve()
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    discovered = sources
    if discovered is None:
        discovered, data_liste = scopri_fonti(include_lists, reader)
    if not {'ema', 'aifa'} <= set(discovered):
        raise ValueError('Le fonti EMA e AIFA sono obbligatorie')
    if include_lists and not {'aifa_a', 'aifa_h', 'aifa_cnn'} <= set(discovered):
        raise ValueError('Le tre liste A/H/C(nn) sono richieste (usa --solo-base per ometterle)')
    if include_lists and not data_liste:
        d = {data_liste_da_url(discovered[k]) for k in ('aifa_a', 'aifa_h')}
        if len(d) != 1 or None in d:
            raise ValueError('Data dei dataset A/H non determinabile in automatico')
        data_liste = d.pop()
    for url in discovered.values():
        assicurati_ufficiale(url)

    started = datetime.now(timezone.utc).isoformat(timespec='seconds')
    report_path = Path(report_path or (dest_db.parent / 'rapporto_automatico.json')).resolve()
    with tempfile.TemporaryDirectory(prefix='.importazione_', dir=dest_db.parent) as work:
        work = Path(work)
        paths = {}
        metadata = {}
        for kind, url in discovered.items():
            ext = '.json' if kind == 'ema' else '.csv'
            p = work / (kind + ext)
            try:
                metadata[kind] = {'url': url, **salva_dataset(url, p, MAX_BYTES[kind], reader)}
            except Exception as exc:
                raise ValueError(f'Errore durante il download di {kind}: {exc}') from exc
            paths[kind] = p
        validation = valida_file(paths, min_ema=min_ema, min_aifa=min_aifa)
        # Prepara sempre un DB non pubblicato; crea prima un backup coerente se già esiste.
        staged = work / 'staging.sqlite'
        if dest_db.exists():
            with closing(sqlite3.connect(dest_db)) as old, closing(sqlite3.connect(staged)) as target:
                old.backup(target)
        with closing(sqlite3.connect(staged)) as db:
            db.execute('PRAGMA journal_mode=DELETE')
            pipeline.inizializza(db)
            if pilota:
                pipeline.seed_pilota(db)
            # Registra URL degli snapshot effettivamente scaricati, non versioni fisse.
            previous = pipeline.SOURCES.copy()
            try:
                pipeline.SOURCES.update(discovered)
                with db:
                    counts = pipeline.importa(
                        db, paths['ema'], paths['aifa'], paths.get('aifa_a'),
                        paths.get('aifa_h'), data_liste, limite, paths.get('aifa_cnn'))
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('PRAGMA integrity_check fallito')
                if db.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('Foreign key non valide')
                if counts['ema_selezionati'] < min(limite, validation['ema_estratti']):
                    raise ValueError('Numero di medicinali importati troppo basso')
                report = pipeline.esporta_report(db, work / 'report.json')
            finally:
                pipeline.SOURCES.clear()
                pipeline.SOURCES.update(previous)
        manifest = {
            'inizio_utc': started,
            'fine_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'modalita': 'TEST_SIMULATO' if reader is not None or sources is not None else 'FONTI_UFFICIALI',
            'fonti': metadata, 'data_liste_ah': data_liste, 'validazione': validation,
            'importazione': counts, 'archivio': report,
            'avvertenza': 'I match per nome NON sono verificati. Le liste C(nn) e A/H non provano da sole la classificazione attuale.'
        }
        # Un backup semplice del report precedente è evitabile: scriviamo report come file
        # immutabile con hash di run, e manteniamo un puntatore al più recente.
        reports_dir = report_path.parent / 'storico_importazioni'
        reports_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()[:16]
        run_report = reports_dir / f'{started[:10]}_{digest}.json'
        tmp_report = work / 'manifest.json'
        tmp_report.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8')
        # Archivio raw content-addressed, nessuna copia giornaliera se il file è invariato.
        raw_dir = dest_db.parent / 'raw_sha256'
        raw_dir.mkdir(exist_ok=True)
        for kind, p in paths.items():
            ext = '.json' if kind == 'ema' else '.csv'
            target = raw_dir / (metadata[kind]['sha256'] + ext)
            if not target.exists():
                shutil.copyfile(p, target)
        os.replace(staged, dest_db)
        shutil.copyfile(tmp_report, run_report)
        # Il manifest latest viene scritto in modo atomico; in caso di errore
        # il DB resta comunque consistente e i raw rimangono disponibili.
        latest_tmp = report_path.with_suffix(report_path.suffix + '.part')
        try:
            shutil.copyfile(tmp_report, latest_tmp)
            os.replace(latest_tmp, report_path)
        finally:
            latest_tmp.unlink(missing_ok=True)
        return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=DEFAULT_DB)
    ap.add_argument('--limite', type=int, default=30)
    ap.add_argument('--solo-base', action='store_true', help='Acquisisci solo EMA JSON e anagrafica AIFA')
    ap.add_argument('--min-ema', type=int, default=30)
    ap.add_argument('--min-aifa', type=int, default=100)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--senza-pilota', action='store_true')
    args = ap.parse_args(argv)
    if args.limite < 1 or args.min_ema < 1 or args.min_aifa < 1:
        ap.error('Limiti e soglie devono essere > 0')
    try:
        result = aggiorna(args.db, args.limite, not args.solo_base,
                          args.min_ema, args.min_aifa, pilota=not args.senza_pilota,
                          report_path=args.report)
        print(json.dumps({'esito': 'OK', 'fonti': {k:v['url'] for k,v in result['fonti'].items()},
              'record': result['importazione'], 'data_liste_ah': result['data_liste_ah']},
              ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f'IMPORTAZIONE NON ESEGUITA: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
