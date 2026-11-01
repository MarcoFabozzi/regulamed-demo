#!/usr/bin/env python3
"""Archivio completo delle confezioni AIFA in SQLite compresso.

Non deduce collegamenti EMA-AIC, rimborsabilita o classe vigente.
Archivia tutte le AIC univoche del CSV ufficiale e conserva provenienza.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import gzip
import os
import shutil
import sqlite3
import tempfile

ARCHIVE_NAME = 'aifa_confezioni_complete.sqlite.gz'
MIN_ROWS = 100_000
MAX_ARCHIVE_BYTES = 90 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024

SCHEMA = """
PRAGMA page_size=4096;
CREATE TABLE confezioni(
 aic TEXT PRIMARY KEY NOT NULL,
 nome TEXT NOT NULL,
 principio_attivo TEXT,
 descrizione TEXT,
 stato_amministrativo TEXT,
 classe_da_anagrafica TEXT
) WITHOUT ROWID;
CREATE INDEX ix_confezioni_nome ON confezioni(nome);
CREATE INDEX ix_confezioni_principio ON confezioni(principio_attivo);
CREATE TABLE metadati (chiave TEXT PRIMARY KEY, valore TEXT NOT NULL) WITHOUT ROWID;
"""

def crea_archivio(records, destination, source_sha256, source_url, min_rows=MIN_ROWS):
    """Scrive un file nuovo e verificato; nessuna modifica al DB principale."""
    dest=Path(destination)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aifa_archive_',dir=dest.parent) as temp:
        temp=Path(temp)
        database=temp/'confezioni.sqlite'
        with closing(sqlite3.connect(database)) as db:
            db.executescript(SCHEMA)
            rows=[]
            n=0
            for r in records:
                aic=r.get('aic')
                if not isinstance(aic,str) or len(aic)!=9 or not aic.isdigit():
                    raise ValueError('AIC non valida nel CSV ufficiale')
                rows.append((aic,r.get('nome'),r.get('principio_attivo'),
                             r.get('descrizione'),r.get('stato_amministrativo'),
                             r.get('classe_da_anagrafica')))
                n+=1
                if len(rows)>=5000:
                    try:
                        db.executemany('INSERT INTO confezioni VALUES (?,?,?,?,?,?)', rows)
                    except sqlite3.IntegrityError as exc:
                        raise ValueError('AIC duplicata nel CSV ufficiale: verifica necessaria') from exc
                    rows.clear()
            if rows:
                try:db.executemany('INSERT INTO confezioni VALUES (?,?,?,?,?,?)',rows)
                except sqlite3.IntegrityError as exc:
                    raise ValueError('AIC duplicata nel CSV ufficiale: verifica necessaria') from exc
            if n<min_rows:
                raise ValueError(f'Anagrafica completa troppo piccola: {n} righe (minimo {min_rows})')
            db.executemany('INSERT INTO metadati VALUES (?,?)',
                [('sha256_csv',source_sha256),('url_csv',source_url),
                 ('confezioni',str(n)),
                 ('interpretazione','Anagrafica ufficiale; la classe da anagrafica non certifica la rimborsabilita vigente')])
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Integrita SQLite del catalogo AIFA fallita')
            verified=db.execute('SELECT COUNT(*) FROM confezioni').fetchone()[0]
            if verified!=n:
                raise ValueError('Numero confezioni non corrisponde al file di origine')
        compressed=temp/'archive.gz'
        with database.open('rb') as source, compressed.open('wb') as raw:
            with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0,compresslevel=6) as target:
                shutil.copyfileobj(source,target,1024*1024)
        size=compressed.stat().st_size
        if size>MAX_ARCHIVE_BYTES:
            raise ValueError('Archivio troppo grande per la pubblicazione su GitHub')
        digest=sha256()
        with compressed.open('rb') as src:
            for block in iter(lambda:src.read(1024*1024),b''):
                digest.update(block)
        os.replace(compressed,dest)
        return {'confezioni_totali':n,'bytes_compressi':size,
                'sha256_archivio':digest.hexdigest(),
                'file_archivio':dest.name}


def estrai_archivio(source, destination, expected_sha256=None):
    """Decomprimi in sicurezza, controllo dimensione, checksum, SQLite integro."""
    source=Path(source)
    target=Path(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size>MAX_ARCHIVE_BYTES:
        raise ValueError('Dimensione archivio AIFA non ammessa')
    h=sha256()
    with source.open('rb') as blob:
        for chunk in iter(lambda:blob.read(1024*1024),b''):
            h.update(chunk)
    if expected_sha256 and h.hexdigest()!=expected_sha256:
        raise ValueError('SHA-256 archivio AIFA differente dal report ufficiale')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp_name=tempfile.mkstemp(prefix='aifa_extract_',suffix='.sqlite',dir=target.parent)
    os.close(fd)
    tmp=Path(tmp_name)
    try:
        total=0
        with gzip.open(source,'rb') as gz, tmp.open('wb') as out:
            while True:
                chunk=gz.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>MAX_UNCOMPRESSED_BYTES:
                    raise ValueError('Archivio AIFA decompresso troppo grande')
                out.write(chunk)
        with closing(sqlite3.connect(tmp.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Archivio AIFA non integro')
            total_rows=db.execute('SELECT COUNT(*) FROM confezioni').fetchone()[0]
            if total_rows<MIN_ROWS:
                raise ValueError('Archivio AIFA incompleto')
        os.replace(tmp,target)
        return total_rows
    finally:
        tmp.unlink(missing_ok=True)


def ricerca_confezioni(db_file,text,limit=150):
    """Solo ricerche parametrizzate, mai caricamento di 160k record in memoria."""
    text=str(text or '').strip()
    if len(text)<2:
        return []
    # Escape dei wildcard SQL per ottenere ricerche letterali
    pattern='%'+text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    with closing(sqlite3.connect(Path(db_file).as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return [dict(row) for row in db.execute(
            """SELECT aic,nome,principio_attivo,descrizione,stato_amministrativo,
                      classe_da_anagrafica
               FROM confezioni
               WHERE aic LIKE ? ESCAPE '\\' OR nome LIKE ? ESCAPE '\\'
                  OR principio_attivo LIKE ? ESCAPE '\\'
               ORDER BY nome,aic LIMIT ?""",
            (pattern,pattern,pattern,max(1,min(200,int(limit)))))]
