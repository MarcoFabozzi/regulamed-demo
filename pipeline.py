#!/usr/bin/env python3
"""Pipeline EMA–AIFA per una tesi: acquisizione, staging e riconciliazione conservativa.

Stdlib only. Nessun dato medico/regolatorio viene dedotto dal nome. Le liste A/H
sono snapshot datati, non determine e non forniscono informazioni su assenti.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
RAW = BASE / 'data' / 'raw'
DEFAULT_DB = BASE / 'risultati' / 'staging_ema_aifa.sqlite'
SEED = BASE / 'dataset_verificato.json'
SOURCES = {
    'ema': 'https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json',
    'aifa': 'https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv',
    'aifa_cnn': 'https://www.aifa.gov.it/documents/20142/847358/lista_farmaci_valutati_inserimento_classe_Cnn_14.10.2026.csv',
    'aifa_a': 'https://www.aifa.gov.it/documents/20142/3815901/Classe-A_per_nome-commerciale_30-6-2026.csv',
    'aifa_h': 'https://www.aifa.gov.it/documents/20142/3815901/Classe-H_per_nome-commerciale_30-6-2026.csv',
}
# Gli URL A/H e C(nn) sono datati e vanno ricontrollati periodicamente sulla pagina AIFA.
ALLOWED_HOSTS = {'www.ema.europa.eu', 'www.aifa.gov.it', 'drive.aifa.gov.it'}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS acquisizioni(
  id TEXT PRIMARY KEY, tipo TEXT NOT NULL, origine TEXT NOT NULL,
  sha256 TEXT NOT NULL, data_acquisizione TEXT NOT NULL,
  data_riferimento TEXT, etichetta TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ema_medicinali(
  ema_id TEXT PRIMARY KEY, nome TEXT NOT NULL, principio_attivo TEXT,
  stato_ema TEXT, data_decisione_ue TEXT, indicazione_ema TEXT,
  url_ema TEXT, acquisizione_id TEXT NOT NULL REFERENCES acquisizioni(id)
);
CREATE TABLE IF NOT EXISTS aifa_confezioni(
  aic TEXT PRIMARY KEY, nome TEXT NOT NULL, principio_attivo TEXT,
  descrizione TEXT, stato_amministrativo TEXT,
  classe_da_anagrafica TEXT, acquisizione_id TEXT NOT NULL REFERENCES acquisizioni(id)
);
CREATE TABLE IF NOT EXISTS link_verificati(
  ema_id TEXT NOT NULL, aic TEXT NOT NULL,
  codice_presentazione_ue TEXT NOT NULL, metodo TEXT NOT NULL,
  fonte_provvedimento TEXT NOT NULL, nota TEXT,
  PRIMARY KEY(ema_id,aic)
);
CREATE TABLE IF NOT EXISTS match_candidati(
  ema_id TEXT NOT NULL, aic TEXT NOT NULL, criterio TEXT NOT NULL,
  stato TEXT NOT NULL DEFAULT 'DA_VERIFICARE',
  PRIMARY KEY(ema_id,aic)
);
CREATE TABLE IF NOT EXISTS classi_snapshot(
  aic TEXT NOT NULL, classe TEXT NOT NULL CHECK (classe IN ('A','H')),
  data_riferimento TEXT NOT NULL, acquisizione_id TEXT NOT NULL REFERENCES acquisizioni(id),
  PRIMARY KEY(aic,classe,data_riferimento)
);
CREATE TABLE IF NOT EXISTS eventi_verificati(
  id TEXT PRIMARY KEY, ema_id TEXT NOT NULL, tipo TEXT NOT NULL,
  data_pubblicazione TEXT NOT NULL, data_efficacia TEXT,
  classe_precedente TEXT, classe_nuova TEXT,
  fonte TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS cnn_elenco_storico(
  row_id TEXT PRIMARY KEY, nome TEXT, numero_procedura TEXT, principio_attivo TEXT,
  data_chmp TEXT, data_decisione_ue TEXT, riferimento_atto TEXT,
  acquisizione_id TEXT NOT NULL REFERENCES acquisizioni(id),
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anomalie(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL,
  identificativo TEXT NOT NULL, messaggio TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aifa_nome ON aifa_confezioni(nome);
CREATE INDEX IF NOT EXISTS idx_match_ema ON match_candidati(ema_id);
CREATE VIEW IF NOT EXISTS profili_documentati AS
SELECT e.ema_id, e.nome, e.principio_attivo, e.data_decisione_ue,
       e.stato_ema, l.aic, l.codice_presentazione_ue, l.fonte_provvedimento,
       a.classe_da_anagrafica, a.stato_amministrativo,
       (SELECT classe_nuova FROM eventi_verificati ev
        WHERE ev.ema_id=e.ema_id AND ev.classe_nuova IS NOT NULL
        ORDER BY ev.data_pubblicazione DESC,ev.id DESC LIMIT 1) ultimo_atto_nel_campione
FROM ema_medicinali e
LEFT JOIN link_verificati l ON l.ema_id=e.ema_id
LEFT JOIN aifa_confezioni a ON a.aic=l.aic;
"""


def normalizza(v):
    return re.sub(r'[^A-Z0-9]+', '', str(v or '').upper())


def key(v):
    return re.sub(r'[^a-z0-9]+', '_', str(v or '').casefold().strip()).strip('_')


def testo(v):
    if isinstance(v,list):
        return '; '.join(map(str,v))
    if isinstance(v,dict):
        return json.dumps(v,ensure_ascii=False)
    return str(v).strip() if v is not None else ''


def campo(r, *aliases):
    r2={key(k):v for k,v in r.items() if k is not None}
    for a in aliases:
        v=r2.get(key(a))
        if v is not None and testo(v).strip():
            return testo(v).strip()
    return ''


def data_iso(v):
    if not v:
        return None
    s=testo(v)
    for pattern in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%Y/%m/%d','%d %B %Y','%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s[:19] if 'T' in pattern else s,pattern).date().isoformat()
        except ValueError:
            pass
    # Le date AIFA C(nn) sono spesso in italiano: 25-feb-21, 17-giu-21, ...
    m = re.fullmatch(r'(\d{1,2})[-/ ]([a-zA-Z]{3,9})[-/ ](\d{2}|\d{4})', s.strip())
    if m:
        mesi = {
            'gen': 1, 'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
            'mag': 5, 'may': 5, 'giu': 6, 'jun': 6,
            'lug': 7, 'jul': 7, 'ago': 8, 'aug': 8,
            'set': 9, 'sep': 9, 'ott': 10, 'oct': 10,
            'nov': 11, 'dic': 12, 'dec': 12
        }
        mese = mesi.get(m.group(2).lower()[:3])
        anno = int(m.group(3))
        if len(m.group(3)) == 2:
            anno += 2000 if anno <= 69 else 1900
        if mese:
            try:
                return date(anno, mese, int(m.group(1))).isoformat()
            except ValueError:
                return None
    # ISO timestamp with timezone
    try:
        return datetime.fromisoformat(s.replace('Z','+00:00')).date().isoformat()
    except ValueError:
        return None


def lista_ema(path):
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if isinstance(data,list):
        rows=data
    elif isinstance(data,dict):
        rows=next((data[k] for k in ('data','results','medicines','items','records')
                   if isinstance(data.get(k),list)),None)
        if rows is None:
            # Se cambia il formato, fallire invece di usare valori errati.
            raise ValueError(f'JSON EMA: struttura non riconosciuta. Chiavi: {list(data)[:20]}')
    else:
        raise ValueError('JSON EMA: struttura non riconosciuta')
    for r in rows:
        if not isinstance(r,dict):
            continue
        cat=campo(r,'category')
        if cat and ('human' not in cat.casefold()) and ('umano' not in cat.casefold()):
            continue
        ident=campo(r,'ema_product_number','EMA product number')
        nome=campo(r,'name_of_medicine','medicine_name','Medicine name')
        if not ident or not nome:
            continue
        yield {
            'ema_id':ident,'nome':nome,
            'principio_attivo':campo(r,'active_substance','international_non_proprietary_name_common_name','International non-proprietary name (INN) / common name'),
            'stato_ema':campo(r,'medicine_status','Medicine status'),
            'data_decisione_ue':data_iso(campo(r,'european_commission_decision_date','European Commission decision date')),
            'indicazione_ema':campo(r,'therapeutic_indication','Therapeutic indication'),
            'url_ema':campo(r,'medicine_url','Medicine URL'),
        }


def lista_csv(path):
    raw=Path(path).read_bytes()
    if raw[:4]==b'PK\x03\x04':
        raise ValueError('File ZIP/XLSX, atteso CSV')
    content=None
    for enc in ('utf-8-sig','cp1252'):
        try:
            content=raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise ValueError('CSV: codifica sconosciuta')
    if content.lstrip().lower().startswith('<!doctype html') or content.lstrip().lower().startswith('<html'):
        raise ValueError('Il server ha restituito HTML invece del CSV')
    first=content[:10000]
    sep=max((';',',','\t','|'),key=lambda ch: sum(line.count(ch) for line in first.splitlines()[:8]))
    rdr=csv.DictReader(io.StringIO(content),delimiter=sep)
    if not rdr.fieldnames or len(rdr.fieldnames)<2:
        raise ValueError('CSV senza intestazioni e delimitatore valido')
    return [r for r in rdr if any(testo(v) for v in r.values())]


def lista_aifa(path):
    for r in lista_csv(path):
        aic=campo(r,'CODICE_AIC','AIC','codice aic')
        if not re.fullmatch(r'\d{9}',aic):
            continue
        nome=campo(r,'DENOMINAZIONE','DENOMINAZIONE_FARMACO','nome_commerciale','NOME COMMERCIALE','FARMACO')
        if not nome:
            continue
        yield {'aic':aic,'nome':nome,
               'principio_attivo':campo(r,'PRINCIPIO_ATTIVO','PRINCIPIO ATTIVO'),
               'descrizione':campo(r,'DESCRIZIONE_CONFEZIONE','CONFEZIONE','DESCRIZIONE','DENOMINAZIONE_CONFEZIONE'),
               'stato_amministrativo':campo(r,'STATO_AMMINISTRATIVO','STATO AMMINISTRATIVO'),
               'classe_da_anagrafica':campo(r,'CLASSE','CLASSE_RIMBORSABILITA')}


def lista_ah(path):
    """Trova gli AIC anche quando il CSV A/H è privo di intestazioni standard.

    Prima cerca una colonna AIC esplicita; solo se manca usa il riconoscimento
    conservativo della colonna di numeri a 9 cifre. Un formato ambiguo fallisce.
    """
    rows = lista_csv(path)
    explicit = [campo(r, 'CODICE_AIC', 'AIC', 'CODICE AIC') for r in rows]
    explicit = [x for x in explicit if re.fullmatch(r'\d{9}', x)]
    if explicit:
        yield from explicit
        return

    raw = Path(path).read_bytes()
    data = None
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            data = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if data is None:
        raise ValueError('Codifica liste A/H non riconosciuta')
    sep = max((';',',','\t','|'), key=lambda x: sum(line.count(x) for line in data.splitlines()[:8]))
    records = list(csv.reader(io.StringIO(data), delimiter=sep))
    sample = [r for r in records[:250] if len(r) >= 2]
    if len(sample) < 3:
        raise ValueError('Lista A/H: colonna AIC assente o non riconoscibile')
    scores = []
    for i in range(max(map(len, sample))):
        available = [r[i].strip() for r in sample if i < len(r)]
        hits = sum(bool(re.fullmatch(r'\d{9}', v)) for v in available)
        scores.append((hits, hits/max(len(available),1), i))
    scores.sort(reverse=True)
    best = scores[0]
    if best[0] < 3 or best[1] < .8 or (len(scores)>1 and scores[1][0] == best[0]):
        raise ValueError('Lista A/H: impossibile determinare univocamente la colonna AIC')
    for r in records:
        if best[2] < len(r) and re.fullmatch(r'\d{9}', r[best[2]].strip()):
            yield r[best[2]].strip()


_PROCEDURA_CNN = re.compile(r'^EMEA/H/C/\d{2,6}(?:/[\w.-]+)*$', re.IGNORECASE)


def lista_cnn(path):
    """Legge l'elenco storico AIFA C(nn) anche quando il CSV ha molte colonne
    vuote e gruppi ripetuti. Non usa questa lista per dedurre la classe vigente.

    Il formato ufficiale 2026 deriva da un foglio molto largo: la stessa riga
    può contenere blocchi 'nome;EMEA/H/C/...;ATC;principio;...'. I campi sono
    estratti rispetto alla procedura EMA, non alla posizione della prima colonna.
    E' mantenuto anche il supporto per CSV tabellari semplici, usati nei test.
    """
    raw = Path(path).read_bytes()
    if raw.startswith(b'PK\x03\x04'):
        raise ValueError('Elenco C(nn) in formato ZIP/XLSX, atteso CSV')
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('cp1252')
    if content.lstrip().lower().startswith(('<html', '<!doctype', '<?xml')):
        raise ValueError('Il server ha restituito HTML/XML invece del CSV C(nn)')
    sample = content[:16000]
    delimiter = max((';', ',', '\t', '|'), key=lambda c: sum(line.count(c) for line in sample.splitlines()[:10]))
    if delimiter != ';' and sample.count(';') > 10:
        delimiter = ';'
    rows = csv.reader(io.StringIO(content, newline=''), delimiter=delimiter)
    first = next(rows, None)
    if not first:
        return

    # Formato tabellare semplice: intestazione e record sulla stessa struttura.
    # Il CSV ufficiale AIFA 2026, invece, va interpretato come blocchi affiancati.
    header = {key(v): i for i, v in enumerate(first) if v.strip()}
    simple = ('numero_procedura' in header and
              any(k in header for k in ('denominazione', 'denominazione_farmaco')))
    if simple:
        for values in rows:
            r = {col: values[i] if i < len(values) else '' for col, i in header.items()}
            nome = campo(r, 'denominazione', 'denominazione_farmaco', 'farmaco')
            procedura = campo(r, 'numero_procedura')
            if not nome or not _PROCEDURA_CNN.fullmatch(procedura):
                continue
            atto = campo(r, 'provvedimento_aifa', 'estremi_del_provvedimento_aifa')
            canonico = {
                'nome': nome,
                'numero_procedura': procedura,
                'principio_attivo': campo(r, 'principio_attivo'),
                'data_chmp': data_iso(campo(r, 'data_parere_chmp', 'data_parere_positivo_chmp')),
                'data_decisione_ue': data_iso(campo(r, 'data_decisione_comunitaria', 'data_decisione_ue')),
                'riferimento_atto': atto,
            }
            yield {**canonico, 'raw_json': json.dumps(canonico, ensure_ascii=False, sort_keys=True)}
        return

    # CSV AIFA C(nn) 2026: blocchi multipli, con il numero di procedura EMA
    # immediatamente dopo la denominazione e gli altri campi a offset stabili.
    # Anche se gli offset si spostano per le molte celle vuote, l'ancora EMA no.
    for values in rows:
        def at(index):
            return values[index].strip() if 0 <= index < len(values) else ''
        for i, value in enumerate(values):
            procedura = value.strip()
            if not _PROCEDURA_CNN.fullmatch(procedura):
                continue
            nome = at(i - 1)
            principio = at(i + 2)
            # Evita record ottenuti da una riga danneggiata o priva dei dati
            # minimi. I collegamenti alle confezioni NON vengono dedotti qui.
            if not nome or len(nome) > 200 or not principio:
                continue
            atto_url = at(i + 13)
            if not (atto_url.startswith('https://') or atto_url.startswith('http://')):
                atto_url = ''
            numero_atto = at(i + 11)
            data_atto = at(i + 12)
            riferimento = atto_url or ' '.join(x for x in (numero_atto, data_atto) if x)
            canonico = {
                'nome': nome,
                'numero_procedura': procedura,
                'principio_attivo': principio,
                'data_chmp': data_iso(at(i + 4)),
                'data_decisione_ue': data_iso(at(i + 5)),
                'riferimento_atto': riferimento,
            }
            dettagli = {
                'codice_atc': at(i + 1),
                'titolare': at(i + 3),
                'data_guue': data_iso(at(i + 6)),
                'data_cts': data_iso(at(i + 7)),
                'tipologia': at(i + 8),
                'aic_farmaco': at(i + 9),
                'numero_ue_confezioni': at(i + 10),
                'numero_provvedimento': numero_atto,
                'data_provvedimento': data_iso(data_atto),
                'url_provvedimento': atto_url,
                'numero_guri': at(i + 14),
                'data_guri': data_iso(at(i + 15)),
            }
            yield {**canonico, 'raw_json': json.dumps({**canonico, **dettagli}, ensure_ascii=False, sort_keys=True)}


def registra_fonte(db, tipo, path, url=None, data_riferimento=None, etichetta=None):
    path=Path(path)
    if etichetta is None:
        etichetta='FIXTURE_TEST_NON_UFFICIALE' if 'fixture' in path.name.lower() else 'FILE_LOCALE_ORIGINE_DICHIARATA_NON_AUTENTICATA'
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    sid=tipo+'_'+digest[:24]
    db.execute('INSERT OR IGNORE INTO acquisizioni VALUES (?,?,?,?,?,?,?)',
               (sid,tipo,url or str(path.resolve()),digest,datetime.now(timezone.utc).isoformat(timespec='seconds'),data_riferimento,etichetta))
    return sid


def inizializza(db):
    db.executescript(SCHEMA)
    return db


def seed_pilota(db, source_path=SEED):
    """Carica i tre farmaci verificati manualmente in precedenza; non un nuovo download."""
    data=json.loads(Path(source_path).read_text(encoding='utf8'))
    sid=registra_fonte(db,'pilot',source_path,etichetta='PILOTA_MANUALE_PRECEDENTE')
    origin={f['id']:f['url'] for f in data['fonti']}
    for m in data['medicinali']:
        db.execute('''INSERT INTO ema_medicinali VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(ema_id) DO NOTHING''',
          (m['ema_product_number'],m['denominazione'],m.get('principio_attivo'),
           'NON_VERIFICATO_ATTUALE',m.get('data_decisione_ue'),m.get('indicazione_sintesi'),origin.get(m['fonte_ema']),sid))
    byid={m['id']:m for m in data['medicinali']}
    for c in data['confezioni']:
        m=byid[c['medicinale_id']]
        db.execute('''INSERT INTO aifa_confezioni VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(aic) DO NOTHING''',
          (c['aic'],m['denominazione'],m['principio_attivo'],c.get('descrizione'),None,None,sid))
        db.execute('INSERT OR IGNORE INTO link_verificati VALUES (?,?,?,?,?,?)',
            (m['ema_product_number'],c['aic'],c['eu'],'COPPIA_ESPLICITA_NELLO_STESSO_ATTO',origin[c['fonte_match']],
             'Coppia da determina GU riportata nel dataset pilota manuale'))
    for e in data['provvedimenti']:
        m=byid[e['medicinale_id']]
        db.execute('INSERT OR IGNORE INTO eventi_verificati VALUES (?,?,?,?,?,?,?,?,?)',
            (e['id'],m['ema_product_number'],e['tipo'],e['data_pubblicazione'],e.get('data_efficacia'),
             e.get('classe_precedente'),e.get('classe_nuova'),origin[e['fonte']],e.get('note')))


def importa(db, ema_path, aifa_path, a_path=None, h_path=None, data_ah=None, limite=30, cnn_path=None):
    sid_e=registra_fonte(db,'ema',ema_path,SOURCES['ema'])
    sid_a=registra_fonte(db,'aifa',aifa_path,SOURCES['aifa'])
    es=list(lista_ema(ema_path))
    ps=list(lista_aifa(aifa_path))
    if not es: raise ValueError('EMA: nessun record valido, importazione interrotta')
    if not ps: raise ValueError('AIFA: nessuna confezione valida, importazione interrotta')
    # Seleziona prima il campione: non caricare le ~centinaia di migliaia di
    # confezioni AIFA nel piccolo database sperimentale.
    nomes_aifa={normalizza(p['nome']) for p in ps}
    pilot_ids={r[0] for r in db.execute('SELECT DISTINCT ema_id FROM link_verificati')}
    pilot_aics={r[0] for r in db.execute('SELECT DISTINCT aic FROM link_verificati')}
    es.sort(key=lambda e: (0 if e['ema_id'] in pilot_ids else 1 if normalizza(e['nome']) in nomes_aifa else 2,e['nome']))
    chosen=es[:max(limite,len(pilot_ids))]
    nomi_selezionati={normalizza(e['nome']) for e in chosen}
    selezionate=[p for p in ps if normalizza(p['nome']) in nomi_selezionati or p['aic'] in pilot_aics]
    # Importa le sole confezioni pertinenti al campione.
    for p in selezionate:
        old=db.execute('SELECT nome FROM aifa_confezioni WHERE aic=?',(p['aic'],)).fetchone()
        if old and normalizza(old[0]) != normalizza(p['nome']):
            db.execute('INSERT INTO anomalie(tipo,identificativo,messaggio) VALUES (?,?,?)',
                       ('NOME_AIC_DIVERSO',p['aic'],f"Prima {old[0]}; nuovo {p['nome']}. Bloccato aggiornamento, verificare manualmente."))
            continue  # Non sovrascrivere una AIC preesistente in conflitto.
        db.execute('''INSERT INTO aifa_confezioni VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(aic) DO UPDATE SET
           nome=excluded.nome, principio_attivo=excluded.principio_attivo,
           descrizione=excluded.descrizione, stato_amministrativo=excluded.stato_amministrativo,
           classe_da_anagrafica=excluded.classe_da_anagrafica,
           acquisizione_id=excluded.acquisizione_id''',
           (p['aic'],p['nome'],p['principio_attivo'],p['descrizione'],p['stato_amministrativo'],p['classe_da_anagrafica'],sid_a))
    for e in chosen:
        old=db.execute('SELECT nome FROM ema_medicinali WHERE ema_id=?',(e['ema_id'],)).fetchone()
        if old and normalizza(old[0]) != normalizza(e['nome']):
            db.execute('INSERT INTO anomalie(tipo,identificativo,messaggio) VALUES (?,?,?)',
                       ('NOME_EMA_ID_DIVERSO',e['ema_id'],f"Prima {old[0]}; nuovo {e['nome']}. Bloccato aggiornamento."))
            continue
        db.execute('''INSERT INTO ema_medicinali VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(ema_id) DO UPDATE SET
           nome=excluded.nome, principio_attivo=excluded.principio_attivo,
           stato_ema=excluded.stato_ema, data_decisione_ue=excluded.data_decisione_ue,
           indicazione_ema=excluded.indicazione_ema, url_ema=excluded.url_ema,
           acquisizione_id=excluded.acquisizione_id''',
           (e['ema_id'],e['nome'],e['principio_attivo'],e['stato_ema'],e['data_decisione_ue'],e['indicazione_ema'],e['url_ema'],sid_e))
    db.execute('DELETE FROM match_candidati')
    ps_by_name={}
    for p in selezionate: ps_by_name.setdefault(normalizza(p['nome']),[]).append(p)
    for e in chosen:
        for p in ps_by_name.get(normalizza(e['nome']),[]):
            verified=db.execute('SELECT 1 FROM link_verificati WHERE ema_id=? AND aic=?',(e['ema_id'],p['aic'])).fetchone()
            if verified: continue
            a=normalizza(e['principio_attivo']); b=normalizza(p['principio_attivo'])
            criterion='NOME_ESATTO_E_PRINCIPIO_ATTIVO_COMPATIBILE' if a and b and a==b else 'SOLO_NOME_ESATTO'
            db.execute('INSERT OR IGNORE INTO match_candidati VALUES (?,?,?,?)',
                       (e['ema_id'],p['aic'],criterion,'DA_VERIFICARE'))
    if cnn_path:
        sid_cnn=registra_fonte(db,'aifa_cnn',cnn_path,SOURCES['aifa_cnn'],
                    etichetta='ELENCO_STORICO_CNN_NON_CLASSIFICAZIONE_ATTUALE')
        for r in lista_cnn(cnn_path):
            if normalizza(r['nome']) not in nomi_selezionati:
                continue
            rowid=hashlib.sha256(r['raw_json'].encode('utf8')).hexdigest()[:32]
            db.execute('INSERT OR IGNORE INTO cnn_elenco_storico VALUES (?,?,?,?,?,?,?,?,?)',
               (rowid,r['nome'],r['numero_procedura'],r['principio_attivo'],r['data_chmp'],
                r['data_decisione_ue'],r['riferimento_atto'],sid_cnn,r['raw_json']))
    for classe,path in [('A',a_path),('H',h_path)]:
        if not path: continue
        if not data_ah: raise ValueError('Per la lista A/H serve --data-liste: data effettiva del dataset, NON data di download')
        sid=registra_fonte(db,'classe_'+classe,path,SOURCES.get('aifa_'+classe.lower()),data_riferimento=data_ah,
                          etichetta='LISTA_AIFA_AH_SNAPSHOT_NON_DETERMINA')
        for aic in lista_ah(path):
            if db.execute('SELECT 1 FROM aifa_confezioni WHERE aic=?',(aic,)).fetchone():
                db.execute('INSERT OR IGNORE INTO classi_snapshot VALUES (?,?,?,?)',(aic,classe,data_ah,sid))
    return {'ema_file':len(es),'aifa_file':len(ps),'ema_selezionati':len(chosen),'confezioni_aifa_selezionate':len(selezionate),
            'candidati':db.execute('SELECT COUNT(*) FROM match_candidati').fetchone()[0],
            'verificati':db.execute('SELECT COUNT(*) FROM link_verificati').fetchone()[0]}


def esporta_report(db, out):
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    data={}
    for tab in ('acquisizioni','ema_medicinali','aifa_confezioni','link_verificati','match_candidati','classi_snapshot','cnn_elenco_storico','eventi_verificati','anomalie'):
        data[tab]=db.execute(f'SELECT COUNT(*) FROM {tab}').fetchone()[0]
    examples=[dict(zip([c[0] for c in db.execute('SELECT * FROM profili_documentati LIMIT 1').description],row))
              for row in db.execute('SELECT * FROM profili_documentati LIMIT 20')]
    data['esempi']=examples
    data['nota']='Dati dei soli atti e snapshot disponibili; non certifica stato vigente. Match candidati NON verificati.'
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    return data


def scarica(output_dir, nomi):
    output_dir=Path(output_dir); output_dir.mkdir(parents=True,exist_ok=True)
    for n in nomi:
        url=SOURCES[n]
        host=urlparse(url).hostname
        if host not in ALLOWED_HOSTS: raise ValueError('Host non autorizzato: '+str(host))
        ext='.json' if n=='ema' else '.csv'
        dest=output_dir/(n+ext)
        req=Request(url,headers={'User-Agent':'TesiFarmaciResearch/0.3 (academic, public data)','Accept':'application/json,text/csv,application/octet-stream,*/*'})
        tmp=dest.with_suffix(dest.suffix+'.part')
        try:
            with urlopen(req,timeout=50) as resp, open(tmp,'wb') as w:
                final=urlparse(resp.geturl()).hostname
                if final not in ALLOWED_HOSTS: raise ValueError('Redirect fuori dai domini ufficiali')
                total=0
                while True:
                    buf=resp.read(1024*1024)
                    if not buf: break
                    total+=len(buf)
                    if total>200_000_000: raise ValueError('Risorsa oltre 200 MB')
                    w.write(buf)
            head=tmp.read_bytes()[:64].lstrip().lower()
            if head.startswith(b'<html') or head.startswith(b'<!doctype'):
                raise ValueError('Errore: invece del dataset è stata restituita una pagina HTML')
            os.replace(tmp,dest)
            print(f'{n}: {dest} ({total:,} byte)')
        except Exception as ex:
            if tmp.exists(): tmp.unlink()
            raise RuntimeError(f'Download {n} fallito: {ex}. Usa il download manuale dal sito ufficiale.') from ex


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    d=sub.add_parser('scarica',help='Scarica dataset dai soli URL ufficiali pubblici')
    d.add_argument('--cartella',type=Path,default=RAW)
    d.add_argument('--fonti',nargs='+',choices=list(SOURCES),default=['ema','aifa'])
    i=sub.add_parser('importa',help='Normalizza JSON EMA + CSV AIFA, separa match verificati/candidati')
    i.add_argument('--ema',type=Path,required=True)
    i.add_argument('--aifa',type=Path,required=True)
    i.add_argument('--aifa-a',type=Path)
    i.add_argument('--aifa-h',type=Path)
    i.add_argument('--aifa-cnn',type=Path,help='Elenco storico C(nn) facoltativo: non prova classificazione vigente')
    i.add_argument('--data-liste',help='Es. 2026-06-30: data effettiva delle liste A/H')
    i.add_argument('--limite',type=int,default=30)
    i.add_argument('--db',type=Path,default=DEFAULT_DB)
    i.add_argument('--senza-pilota',action='store_true')
    i.add_argument('--report',type=Path,default=BASE/'risultati'/'report_importazione.json')
    q=sub.add_parser('profilo',help='Mostra un medicinale e le fonti dal database')
    q.add_argument('farmaco')
    q.add_argument('--db',type=Path,default=DEFAULT_DB)
    args=p.parse_args(argv)
    if args.command=='scarica':
        scarica(args.cartella,args.fonti)
    elif args.command=='importa':
        if args.limite<=0: p.error('--limite deve essere maggiore di zero')
        if args.data_liste and not data_iso(args.data_liste): p.error('--data-liste deve essere YYYY-MM-DD')
        args.db.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(args.db) as db:
            inizializza(db)
            if not args.senza_pilota: seed_pilota(db)
            with db:
                summary=importa(db,args.ema,args.aifa,args.aifa_a,args.aifa_h,args.data_liste,args.limite,args.aifa_cnn)
            report=esporta_report(db,args.report)
            print(json.dumps({'importazione':summary,'archivio':str(args.db),'report':str(args.report)},ensure_ascii=False,indent=2))
    elif args.command=='profilo':
        with sqlite3.connect(args.db) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT * FROM profili_documentati WHERE upper(nome) LIKE upper(?)',('%'+args.farmaco+'%',)).fetchall()
            if not rows:
                print('Medicinale non presente in questo campione.'); return 1
            for r in rows: print(json.dumps(dict(r),ensure_ascii=False,indent=2))
            print('ATTENZIONE: ultimo atto nel campione; non dichiarazione sulla rimborsabilità attuale.')
    return 0

if __name__=='__main__':
    sys.exit(main())
