#!/usr/bin/env python3
"""Import incrementale conservativo dalla Gazzetta Ufficiale (Serie Generale).
Solo atti pertinenti al campione; gli abbinamenti restano DA_VERIFICARE.
"""
from __future__ import annotations
from datetime import date,datetime,timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlparse,parse_qs,urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
import json,os,re,sqlite3,time

RECENT='https://www.gazzettaufficiale.it/30giorni/serie_generale'
HOST='www.gazzettaufficiale.it'
CODE=re.compile(r'^[0-9]{2}[A-Z][0-9]{5}$')
EMA=re.compile(r'\bEU/\d/\d{2}/\d{4}/\d{3}\b',re.I)
AIC=re.compile(r'A\s*\.?\s*I\s*\.?\s*C\s*\.?\s*(?:n\s*\.?\s*)?[:\s]*(0\d{8})\b',re.I)
CLASS=re.compile(r"classe\s+di\s+rimborsabilit(?:a'|a|à)\s*[:\s-]*(C\s*\(\s*nn\s*\)|A|H|C)(?!\w)",re.I)
SCHEMA='''
CREATE TABLE IF NOT EXISTS gazzetta_atti (
 codice TEXT PRIMARY KEY,data_pubblicazione TEXT NOT NULL,titolo TEXT,tipo TEXT,
 url_ufficiale TEXT NOT NULL,url_testo TEXT NOT NULL,testo_file TEXT NOT NULL,
 sha256_testo TEXT NOT NULL,data_acquisizione_utc TEXT NOT NULL,
 nominativi_campione TEXT NOT NULL,numero_aic_estratte INTEGER NOT NULL DEFAULT 0,
 completezza TEXT NOT NULL DEFAULT 'DA_VERIFICARE');
CREATE TABLE IF NOT EXISTS gazzetta_evidenze_aic (
 codice TEXT NOT NULL REFERENCES gazzetta_atti(codice) ON DELETE CASCADE,
 aic TEXT NOT NULL,presente_in_anagrafica INTEGER NOT NULL,snippet TEXT NOT NULL,
 ipotesi_classe TEXT,stato TEXT NOT NULL DEFAULT 'DA_VERIFICARE',PRIMARY KEY(codice,aic));
CREATE TABLE IF NOT EXISTS gu_allegati (
 codice TEXT NOT NULL,url TEXT NOT NULL,testo_file TEXT NOT NULL,
 sha256 TEXT NOT NULL,acquisito_utc TEXT NOT NULL,PRIMARY KEY(codice,url));
CREATE TABLE IF NOT EXISTS gu_coppie_eu_aic (
 codice TEXT NOT NULL,eu TEXT NOT NULL,aic TEXT NOT NULL,url_allegato TEXT NOT NULL,
 estratto TEXT NOT NULL,aic_in_campione INTEGER NOT NULL,
 stato TEXT NOT NULL DEFAULT 'EVIDENZA_DOCUMENTALE_DA_REVISIONARE',PRIMARY KEY(codice,eu,aic));
'''
class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links=[];self.href=None;self.parts=[];self.text=[];self.skip=0
    def handle_starttag(self,t,attrs):
        if t=='a':self.href=dict(attrs).get('href');self.parts=[]
        if t in ('script','style','nav'):self.skip+=1
        if t in ('p','div','br','li','tr','h1','h2'):self.text.append('\n')
    def handle_data(self,v):
        if self.href is not None:self.parts.append(v)
        if not self.skip:self.text.append(v)
    def handle_endtag(self,t):
        if t=='a' and self.href is not None:
            self.links.append((self.href,' '.join(' '.join(self.parts).split())))
            self.href=None;self.parts=[]
        if t in ('script','style','nav') and self.skip:self.skip-=1
    def result(self):
        return '\n'.join(line.strip() for line in ''.join(self.text).splitlines() if line.strip())

def safe(u):
    p=urlparse(u)
    if p.scheme!='https' or p.hostname!=HOST or p.username or p.password or p.port not in (None,443):
        raise ValueError('URL non ufficiale GU')
    return u

def fetch(u,limit=3_000_000):
    safe(u)
    req=Request(u,headers={'User-Agent':'RegulaMed/1.0 academic public regulatory data',
                           'Accept':'text/html,text/plain'})
    for attempt in range(4):
        try:
            with urlopen(req,timeout=30) as resp:
                safe(resp.geturl())
                ctype=resp.headers.get('Content-Type','').lower()
                if ctype and not ('text/html' in ctype or 'text/plain' in ctype):
                    raise ValueError('Contenuto GU non HTML')
                b=resp.read(limit+1)
                if not b or len(b)>limit:raise ValueError('Documento GU vuoto o eccessivo')
                return b.decode('utf-8-sig',errors='replace')
        except (HTTPError,URLError,TimeoutError) as e:
            if isinstance(e,HTTPError) and e.code not in (408,429,500,502,503,504):raise
            if attempt==3:raise
            time.sleep((2,5,10)[attempt])

def ident(u):
    p=urlparse(safe(u))
    m=re.search(r'/eli/id/(\d{4})/(\d{2})/(\d{2})/([0-9]{2}[A-Z][0-9]{5})/sg/?$',p.path,re.I)
    if m:return m[4].upper(),'-'.join((m[1],m[2],m[3]))
    q=parse_qs(p.query)
    code=(q.get('atto.codiceRedazionale') or q.get('art.codiceRedazionale') or [None])[0]
    when=(q.get('atto.dataPubblicazioneGazzetta') or q.get('art.dataPubblicazioneGazzetta') or [None])[0]
    if code and when and CODE.fullmatch(code.upper()):
        date.fromisoformat(when);return code.upper(),when
    return None

def candidates(max_issues=30):
    p=Parser();p.feed(fetch(RECENT))
    issues=[]
    for href,title in p.links:
        u=urljoin(RECENT,href)
        try:safe(u)
        except ValueError:continue
        parts=urlparse(u);q=parse_qs(parts.query)
        when=(q.get('dataPubblicazioneGazzetta') or [None])[0]
        number=(q.get('numeroGazzetta') or [None])[0]
        if 'caricaDettaglio' in parts.path and 'serie_generale' in parts.path and when and number:
            try:date.fromisoformat(when);issues.append((when,number,u))
            except ValueError:pass
    if not issues:raise ValueError('Indice GU senza fascicoli riconoscibili')
    outputs={}
    for when,num,url in sorted(set(issues),reverse=True)[:max_issues]:
        p=Parser();p.feed(fetch(url))
        for href,label in p.links:
            u=urljoin(url,href)
            try:match=ident(u)
            except ValueError:continue
            if not match or not re.search(r'farmac|medicinal|riclassific|rimborsabilit|autorizzazione all.immissione',label,re.I):continue
            code,when2=match
            outputs.setdefault(code,(when2,label))
        time.sleep(0.2)
    return outputs,len(set(issues))

def aic_evidences(t):
    matches={}
    for m in AIC.finditer(t):
        around=t[max(0,m.start()-150):min(len(t),m.end()+420)]
        c=CLASS.search(t[m.end():m.end()+270])
        matches.setdefault(m[1],(' '.join(around.split()),c[1].replace(' ','') if c else None))
    return matches

def coppie_eu_aic_da_testo(text):
    """Coppie ricavate esclusivamente da occorrenze ravvicinate nel testo.
    Evidenze da revisionare, non conferme di abbinamento normativo."""
    pairs = {}
    for m in EMA.finditer(text):
        tail = text[m.end():m.end()+150]
        a = AIC.search(tail)
        next_eu = EMA.search(tail)
        if not a or (next_eu and next_eu.start() < a.start()):
            continue
        eu, aic = m.group(0).upper(), a.group(1)
        if (eu, aic) not in pairs:
            pairs[(eu, aic)] = ' '.join(
                text[max(0,m.start()-35):m.end()+a.end()+45].split())
    return pairs


def atti_pertinenti(acts, nomi):
    """Seleziona da TUTTO il sommario; nessun taglio arbitrario ai primi 100 atti."""
    def matches(title):
        return any(
            len(name) >= 3 and re.search(r'(?<!\\w)' + re.escape(name) + r'(?!\\w)', title, re.I)
            for name in nomi
        )
    return [(code, when, title) for code, (when, title) in acts.items()
            if matches(title)]


def annex_urls(html,code,when):
    p=Parser();p.feed(html);ans=[]
    for href,label in p.links:
        if 'allegat' not in label.lower():continue
        u=urljoin('https://'+HOST+'/',href)
        try:safe(u)
        except ValueError:continue
        p2=urlparse(u);q=parse_qs(p2.query)
        if '/caricaArticolo' in p2.path and q.get('art.codiceRedazionale',[None])[0]==code and q.get('art.dataPubblicazioneGazzetta',[None])[0]==when and u not in ans:
            ans.append(u)
    return ans

def run(con,work,limit_issues=30):
    con.executescript(SCHEMA)
    names=[r[0] for r in con.execute('SELECT nome FROM ema_medicinali') if r[0]]
    def matched(t):return [n for n in names if len(n)>=3 and re.search(r'(?<!\w)'+re.escape(n)+r'(?!\w)',t,re.I)]
    acts,issues=candidates(limit_issues)
    known={r[0] for r in con.execute('SELECT codice FROM gazzetta_atti')}
    pertinent = atti_pertinenti(acts, names)
    outdir=Path(work)/'gazzetta_testi';outdir.mkdir(exist_ok=True)
    new=0;pending=0;new_annex=0
    for code,when,title in pertinent:
        if code in known:continue
        typ=re.search(r'\b(DETERMINA|COMUNICATO|DECRETO)\b',title,re.I)
        typ=typ[1].upper() if typ else 'DETERMINA'
        url='https://'+HOST+'/eli/id/'+when.replace('-','/')+'/'+code+'/sg'
        article='https://'+HOST+'/atto/serie_generale/caricaArticoloDefault/originario?'+urlencode({
            'atto.codiceRedazionale':code,'atto.dataPubblicazioneGazzetta':when,'atto.tipoProvvedimento':typ})
        p=Parser();p.feed(fetch(article));txt=p.result()
        if len(txt)<100 or not re.search(r'\b(art|determina|medicinal|farmac|a\.?i\.?c\.?)\b',txt,re.I):
            raise ValueError('Testo incompleto per '+code)
        mentions=matched(txt+' '+title)
        if not mentions:continue
        ev=aic_evidences(txt);digest=sha256(txt.encode()).hexdigest()
        name=code+'_'+digest[:12]+'.txt';(outdir/name).write_text(txt,encoding='utf-8')
        con.execute('''INSERT INTO gazzetta_atti VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(
            code,when,title,typ,url,article,'gazzetta_testi/'+name,digest,
            datetime.now(timezone.utc).isoformat(),json.dumps(mentions,ensure_ascii=False),
            len(ev),'TESTO_PARZIALE_O_NON_VERIFICATO'))
        for aic,(snip,cls) in ev.items():
            in_db=con.execute('SELECT 1 FROM aifa_confezioni WHERE aic=?',(aic,)).fetchone() is not None
            con.execute('''INSERT INTO gazzetta_evidenze_aic
              (codice,aic,presente_in_anagrafica,snippet,ipotesi_classe) VALUES(?,?,?,?,?)''',
              (code,aic,int(in_db),snip,cls))
        new+=1
        menu='https://'+HOST+'/atto/vediMenuHTML?'+urlencode({
            'atto.codiceRedazionale':code,'atto.dataPubblicazioneGazzetta':when,
            'tipoSerie':'serie_generale','tipoVigenza':'originario'})
        links=annex_urls(fetch(menu),code,when)
        for link in links:
            parser=Parser();parser.feed(fetch(link,limit=4_000_000));text=parser.result()
            if len(text)<30:raise ValueError('Allegato incompleto: '+code)
            hash_=sha256(text.encode()).hexdigest();name=code+'_allegato_'+hash_[:12]+'.txt'
            (outdir/name).write_text(text,encoding='utf8')
            con.execute('INSERT INTO gu_allegati VALUES(?,?,?,?,?)',(
                code,link,'gazzetta_testi/'+name,hash_,datetime.now(timezone.utc).isoformat()))
            for (eu,aic),estratto in coppie_eu_aic_da_testo(text).items():
                known_aic=con.execute('SELECT 1 FROM aifa_confezioni WHERE aic=?',(aic,)).fetchone() is not None
                cur=con.execute('''INSERT OR IGNORE INTO gu_coppie_eu_aic
                  (codice,eu,aic,url_allegato,estratto,aic_in_campione) VALUES (?,?,?,?,?,?)''',(
                    code,eu,aic,link,estratto,int(known_aic)))
                pending+=cur.rowcount
            new_annex+=1
        time.sleep(0.2)
    return {'fascicoli_disponibili':issues,'atti_candidati':len(acts),
            'atti_con_medicinale_del_campione_nel_titolo':len(pertinent),
            'atti_gia_archiviati_tra_i_pertinenti':sum(code in known for code,_,_ in pertinent),
            'nuovi_atti':new,'allegati_nuovi':new_annex,
            'coppie_documentali_da_revisionare':pending,
            'criterio_selezione':'Corrispondenza nome medicinale nel titolo; screening selettivo non esaustivo',
            'fonti':{'gazzetta':{'url':RECENT}}}
