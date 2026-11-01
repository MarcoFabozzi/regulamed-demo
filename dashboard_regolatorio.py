#!/usr/bin/env python3
"""Dashboard di ricerca e audit per la tesi EMA–AIFA–GU; SOLO LETTURA.
Esegui: python -m streamlit run dashboard_regolatorio.py --server.port 8504
"""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import json
import sqlite3
import streamlit as st
from qualita_database import (DEFAULT_DB, audit, inventario, relazione_md, csv_protetto,
                              connessione_sola_lettura, presenti, relazione_html)

st.set_page_config(page_title='RegulaMed · Osservatorio farmaci',page_icon='💊',layout='wide',
                   initial_sidebar_state='expanded')
st.markdown('''<style>
:root { --navy:#132940; --mint:#148a7b; }
.stApp {background: #f6f8fb; color: #17263a;}
.block-container{padding-top:1.45rem; padding-bottom:4rem; max-width:1480px;}
section[data-testid="stSidebar"]{background:#10283b;}
section[data-testid="stSidebar"] *{color:#e8f0f5!important;}
section[data-testid="stSidebar"] [data-baseweb="select"] *{color:#17263a!important;}
section[data-testid="stSidebar"] .stAlert *{color:#17263a!important;}
.hero {background:linear-gradient(120deg,#10283b,#1e5362 70%,#17675f);
 color:white;border-radius:20px;padding:26px 34px;margin:0 0 22px 0; box-shadow:0 12px 35px #0b243017;}
.hero h1 {color:white!important;font-size:2rem;margin:0 0 6px 0;font-weight:760;}
.hero p{color:#e0eeef;margin:0;font-size:.99rem;}
.eyebrow{color:#acf0db;font-size:.78rem;font-weight:700;letter-spacing:.13em;text-transform:uppercase;margin-bottom:8px;}
.chip{display:inline-block;background:#ffffff1b;border:1px solid #ffffff55;padding:4px 10px;border-radius:24px;font-size:.75rem;margin-top:13px;color:#e5fcf5;}
[data-testid="stMetric"]{background:white; border:1px solid #e6edf2; padding:16px 19px;border-radius:14px;box-shadow:0 3px 15px #10283b0b;}
[data-testid="stMetricLabel"]{color:#667e8c;}
[data-testid="stMetricValue"]{color:#10283b;font-weight:760;}
h1,h2,h3 {color:#1a3147;}
.stTabs [data-baseweb="tab-list"]{background:white;padding:7px;border-radius:12px;gap:4px;border:1px solid #e3eaf0;}
.stTabs [data-baseweb="tab"]{border-radius:8px;}
[data-testid="stDataFrame"]{background:white;border-radius:11px;}
.section-eyebrow{font-size:.78rem;color:#148a7b;font-weight:720;text-transform:uppercase;letter-spacing:.08em;}
</style>''',unsafe_allow_html=True)

BASE=Path(__file__).resolve().parent
DATA=BASE/'risultati'


def safe_url(url):
    if not isinstance(url,str) or not url:
        return None
    try:
        p=urlsplit(url)
        return url if p.scheme=='https' and p.hostname in {
            'www.ema.europa.eu','www.aifa.gov.it','drive.aifa.gov.it',
            'www.gazzettaufficiale.it','ec.europa.eu','trovanorme.aifa.gov.it'} and not p.username and not p.password else None
    except ValueError:
        return None


def query(db, sql, args=()):
    with closing(connessione_sola_lettura(db)) as con:
        return [dict(r) for r in con.execute(sql,args)]


def exists(db,tab):
    with closing(connessione_sola_lettura(db)) as con:
        return tab in presenti(con)


def show_table(data, key=None, height=330):
    if data:
        st.dataframe(data, hide_index=True, use_container_width=True, height=height)
    else:
        st.info('Non sono presenti record per questa selezione.')


st.sidebar.markdown('### ◈ RegulaMed')
st.sidebar.caption('Prototipo di integrazione regolatoria')
local=[('Archivio importato',DATA/'farmaci_automatico.sqlite'),
       ('Archivio dimostrativo',DATA/'staging_ema_aifa.sqlite'),
       ]
choices={name: p for name,p in local if p.is_file()}
if not choices:
    st.error('Nessun database trovato. Il file richiesto è `risultati/farmaci_automatico.sqlite`. Esegui prima l’importazione EMA–AIFA e apri la dashboard dalla stessa cartella del progetto.')
    st.stop()
selection=st.sidebar.selectbox('Origine dei dati',list(choices))
db=choices[selection]
if 'importato' not in selection:
    st.sidebar.warning('Archivio di prova: i dati NON certificano lo stato reale.')
st.sidebar.caption(f'Database: {db.name}')
st.sidebar.caption('Tutte le interrogazioni sono in sola lettura.')

mtime=db.stat().st_mtime_ns

@st.cache_data(ttl=40,show_spinner=False)
def load_report(path,version):
    return audit(path)

@st.cache_data(ttl=40,show_spinner=False)
def load_catalog(path,version):
    return inventario(path)

try:
    report=load_report(db,mtime)
    catalog=load_catalog(db,mtime)
except Exception as exc:
    st.error(f'Impossibile leggere l’archivio: {exc}')
    st.stop()

def formatta_data_italiana(valore):
    """Formatta solo date ISO valide, senza supporre una pubblicazione."""
    if not isinstance(valore, str) or not valore:
        return 'Non disponibile'
    try:
        return datetime.strptime(valore, '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return 'Non disponibile'


def stato_aggiornamento(folder):
    """Legge il manifest dell'ultimo import riuscito, non l'ultimo tentativo."""
    path = folder / 'risultati' / 'rapporto_automatico.json'
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        dt = datetime.fromisoformat(obj['fine_utc'].replace('Z', '+00:00'))
        if dt.tzinfo is None or obj.get('modalita') != 'FONTI_UFFICIALI':
            raise ValueError('provenienza o timestamp non verificabile')
        days = max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()/86400)
        data_pubblicazione = obj.get('data_pubblicazione_liste_ah_verificata')
        return {
            'ora': dt.astimezone(ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y alle %H:%M'),
            'eta_giorni': days,
            'classe_ah': formatta_data_italiana(obj.get('data_liste_ah')),
            'pubblicazione_ah': formatta_data_italiana(data_pubblicazione)
                               if data_pubblicazione else 'Non verificata',
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None



def leggi_stato_fonte(folder, fonte):
    path = folder / 'risultati' / 'stato_fonti' / (fonte + '.json')
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        if obj.get('esito') != 'OK' or obj.get('fonte') != fonte:
            return None
        dt = datetime.fromisoformat(obj['ultimo_successo_utc'].replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y %H:%M')
    except (OSError, ValueError, KeyError, TypeError):
        return None


counts=report['tabelle']; coverage=report['copertura']; evidences=report['evidenze_gazzetta']
last=report['ultima_importazione'] or {}
st.markdown('''<div class="hero"><div class="eyebrow">Tesi sperimentale · Data integration</div>
<h1>RegulaMed <span style="font-weight:400;opacity:.8">/ Osservatorio regolatorio</span></h1>
<p>Autorizzazioni EMA · Confezioni AIFA · Determine della Gazzetta Ufficiale · Provenienza delle informazioni</p>
<div class="chip">Database documentale · Nessuna risposta generata da LLM</div></div>''',unsafe_allow_html=True)
stato = stato_aggiornamento(BASE) if 'importato' in selection else None
if stato:
    a, b, c = st.columns([2, 1, 1])
    a.metric('Ultima importazione congiunta storica', stato['ora'])
    b.metric('AIFA A/H: data di riferimento', stato['classe_ah'])
    c.metric('AIFA A/H: pubblicazione', stato['pubblicazione_ah'])
    st.caption('**Acquisizione** = download e importazione completati. '
               '**Riferimento** = data alla quale si riferiscono gli elenchi A/H. '
               '**Pubblicazione** = quando gli elenchi sono stati resi disponibili, '
               'mostrata soltanto con una fonte verificata. '
               'Queste date non certificano lo stato normativo vigente.')
    if stato['eta_giorni'] > 2:
        st.warning('L’ultima acquisizione completa risale a oltre 48 ore fa. '
                   'Potrebbero esserci stati errori di aggiornamento; consulta GitHub Actions. '
                   'Controlla le singole acquisizioni: EMA, AIFA e Gazzetta sono indipendenti.')
else:
    st.info('Data dell’ultima importazione ufficiale non disponibile o non verificabile.')

if 'importato' in selection:
    st.markdown('#### Aggiornamenti indipendenti per fonte')
    ema_col, aifa_col, gu_col = st.columns(3)
    ema_col.metric('EMA · ultimo successo', leggi_stato_fonte(BASE, 'ema') or 'Da verificare')
    aifa_col.metric('AIFA · ultimo successo', leggi_stato_fonte(BASE, 'aifa') or 'Da verificare')
    gu_col.metric('Gazzetta · ultimo successo', leggi_stato_fonte(BASE, 'gazzetta') or 'Da verificare')
    st.markdown('##### Dettaglio acquisizioni AIFA')
    an_col, ah_col, cnn_col = st.columns(3)
    an_col.metric('Anagrafica confezioni', leggi_stato_fonte(BASE, 'aifa_anagrafica') or 'Da verificare')
    ah_col.metric('Liste A/H', leggi_stato_fonte(BASE, 'aifa_ah') or 'Da verificare')
    cnn_col.metric('Elenco storico C(nn)', leggi_stato_fonte(BASE, 'aifa_cnn') or 'Da verificare')
    st.caption('I tre flussi AIFA hanno download, controlli e tentativi separati. '
               'La data di acquisizione non coincide con la data di riferimento delle liste '
               'né certifica la classificazione corrente.')
    st.caption('I tre orari corrispondono a download/acquisizioni riuscite e pubblicate. '
               'Gli aggiornamenti sono indipendenti; un errore AIFA non blocca EMA o Gazzetta. '
               '«Da verificare» indica che non esiste ancora un report di successo per quella pipeline.')
if 'importato' in selection:
    st.info('**Ambito:** campione locale importato. Le classi A/H sono snapshot datati; l’assenza dalle liste non significa classe C. Le corrispondenze candidate non sono verificate.')
else:
    st.warning('Modalità dimostrativa. Non utilizzare questi risultati per rappresentare la situazione regolatoria attuale.')

m1,m2,m3,m4,m5=st.columns(5)
m1.metric('Medicinali EMA',f"{counts['ema_medicinali']:,}".replace(',','.'))
m2.metric('Confezioni AIFA',f"{counts['aifa_confezioni']:,}".replace(',','.'))
m3.metric('Link documentati',counts['link_verificati'])
m4.metric('Match da verificare',counts['match_candidati'])
m5.metric('Determine GU',counts['gazzetta_atti'])

pan,med_tab,gu_tab,qual_tab,src_tab=st.tabs([
    '▦ Panoramica','⌕ Ricerca medicinali','▤ Gazzetta e allegati','◈ Qualità e report','⇥ Fonti e aggiornamenti'])

with pan:
    st.subheader('Stato della base dati')
    cl,cr=st.columns([1.2,1])
    with cl:
        st.markdown('#### Copertura documentale')
        st.progress(min(coverage['percentuale_medicinali_con_link']/100,1),
                    text=f"{coverage['medicinali_con_link']} medicinali con collegamento EU–AIC documentato ({coverage['percentuale_medicinali_con_link']}%)")
        st.caption('La copertura esprime la proporzione di medicinali del campione con almeno un link registrato; non misura la completezza dei provvedimenti né la qualità delle risposte future.')
        st.markdown('#### Registrazioni disponibili')
        show_table([
            {'Sezione':'Eventi regolatori del pilota','Record':counts['eventi_verificati']},
            {'Sezione':'Snapshot di classe A/H','Record':counts['classi_snapshot']},
            {'Sezione':'Elenco storico C(nn)','Record':counts['cnn_elenco_storico']},
            {'Sezione':'Allegati GU','Record':counts['gu_allegati']},
            {'Sezione':'Coppie EU–AIC negli allegati','Record':counts['gu_coppie_eu_aic']},
        ],height=240)
    with cr:
        st.markdown('#### Qualità e criticità')
        st.metric('Link confermati da coppie GU',evidences['coppie_coerenti'])
        st.metric('Conflitti tra coppie GU e link',evidences['coppie_in_conflitto'])
        if report['integrita']['quick_check'] and report['integrita']['errori_foreign_key']==0:
            st.success('Controllo di integrità SQLite superato.')
        else:
            st.error('Integrità SQLite da verificare: consulta il report dettagliato.')
        st.caption('Sono controlli automatici strutturali, non una revisione legale dei contenuti.')
    st.markdown('#### Cosa possiamo dimostrare')
    st.write('La pipeline acquisisce fonti ufficiali, registra metadati di provenienza, separa i match candidati dalle coppie documentate e conserva uno storico selettivo di atti e snapshot.')
    st.markdown('#### Cosa rimane da completare')
    st.write('Estensione sistematica delle coppie UE–AIC, acquisizione completa delle determine e dei testi di indicazione/condizioni di rimborso, validazione delle decorrenze e costruzione del benchmark.')

with med_tab:
    st.subheader('Catalogo e schede dei medicinali')
    q=st.text_input('Cerca per denominazione, principio attivo o codice EMA',placeholder='Es. Wegovy, lecanemab, EMEA/H/C/',key='med_search')
    if q:
        f=q.casefold().strip()
        filtered=[x for x in catalog if any(f in str(x.get(k) or '').casefold() for k in ('nome','principio_attivo','ema_id'))]
    else:
        filtered=catalog
    st.caption(f'{len(filtered)} medicinali corrispondenti; visualizzo al massimo 250 record nella tabella.')
    show_table(filtered[:250],height=300)
    if filtered:
        selection_med=st.selectbox('Apri la scheda di un medicinale',range(len(filtered)),
                                 format_func=lambda i:f"{filtered[i]['nome']} · {filtered[i]['ema_id']}")
        ema_id=filtered[selection_med]['ema_id']
        med=query(db,'SELECT * FROM ema_medicinali WHERE ema_id=?',(ema_id,))[0]
        st.markdown(f"### {med['nome']}")
        a,b,c=st.columns(3)
        a.write('**Principio attivo**'); a.write(med['principio_attivo'] or 'Non disponibile')
        b.write('**ID EMA**');b.code(ema_id)
        c.write('**Decisione UE nel dataset**');c.write(med['data_decisione_ue'] or 'Non disponibile')
        if exists(db,'ema_revisioni_snapshot'):
            revisions=query(db,'''SELECT prima_osservazione_utc,ultima_osservazione_utc,
                       sha256_riga FROM ema_revisioni_snapshot
                       WHERE ema_id=? ORDER BY prima_osservazione_utc DESC LIMIT 30''',(ema_id,))
            with st.expander(f'Snapshot EMA osservati ({len(revisions)} versioni mostrate)'):
                show_table(revisions,height=210)
                st.caption('Versioni osservate durante i download EMA: non corrispondono '
                           'necessariamente ad atti normativi o prime autorizzazioni.')
        if safe_url(med.get('url_ema')):
            st.link_button('Apri scheda EMA',med['url_ema'])
        details, texttab, chron=st.tabs(['Confezioni e classificazioni','Indicazioni EMA','Eventi e determine'])
        with details:
            ver=query(db,'''SELECT l.aic,l.codice_presentazione_ue,a.descrizione,
                              l.metodo,l.fonte_provvedimento
                FROM link_verificati l LEFT JOIN aifa_confezioni a ON a.aic=l.aic
                WHERE l.ema_id=? ORDER BY l.aic''',(ema_id,))
            st.markdown('**Corrispondenze documentate registrate**')
            show_table(ver, height=230)
            cand=query(db,'''SELECT m.aic,a.descrizione,m.criterio,m.stato
                FROM match_candidati m LEFT JOIN aifa_confezioni a ON a.aic=m.aic
                WHERE m.ema_id=? ORDER BY m.aic LIMIT 250''',(ema_id,))
            with st.expander(f'Collegamenti non verificati ({len(cand)} mostrati)'):
                show_table(cand,height=230)
            if exists(db,'classi_snapshot') and ver:
                snap=query(db,'''SELECT s.aic,s.classe,s.data_riferimento
                    FROM classi_snapshot s JOIN link_verificati l ON s.aic=l.aic
                    WHERE l.ema_id=? ORDER BY s.data_riferimento DESC,s.aic''',(ema_id,))
                st.markdown('**Rilevazioni A/H su liste datate**')
                show_table(snap,height=220)
                st.caption('Snapshot di lista A/H, NON stato regolatorio aggiornato. L’assenza di una AIC non indica classe C.')
        with texttab:
            st.markdown('**Testo terapeutico contenuto nel dataset EMA**')
            st.write(med.get('indicazione_ema') or 'Nessun testo acquisito.')
            st.info('L’indicazione autorizzata UE non equivale necessariamente all’indicazione rimborsata dal SSN italiano.')
        with chron:
            events=query(db,'''SELECT tipo,data_pubblicazione,data_efficacia,
                   classe_precedente,classe_nuova,fonte,note
                   FROM eventi_verificati WHERE ema_id=? ORDER BY data_pubblicazione DESC''',(ema_id,)) if exists(db,'eventi_verificati') else []
            show_table(events,height=250)
            st.caption('Questi sono gli eventi archiviati, non necessariamente l’intero storico né le ultime determine vigenti.')

with gu_tab:
    st.subheader('Determine e allegati Gazzetta Ufficiale')
    audit_path = DATA / 'audit_evidenze_gazzetta.json'
    if audit_path.is_file():
        try:
            audit_doc = json.loads(audit_path.read_text(encoding='utf-8'))
            counts_audit = audit_doc.get('riepilogo', {})
            st.markdown('#### Controllo documentale delle coppie EU–AIC')
            g1,g2,g3=st.columns(3)
            g1.metric('Coerenti nel testo, da revisionare',
                      counts_audit.get('COERENTE_NEL_TESTO_DA_REVISIONARE',0))
            g2.metric('Da verificare con priorità',
                      counts_audit.get('REVISIONE_PRIORITARIA',0))
            g3.metric('Anomalie documentali',
                      counts_audit.get('ANOMALIA_DOCUMENTALE',0))
            st.caption('Controllo automatico di integrità, vicinanza dei codici e possibili conflitti. '
                       'Non equivale a una verifica giuridica della classificazione o rimborsabilità.')
            with st.expander('Apri risultati della revisione documentale'):
                show_table([{'atto':x.get('atto'),'EU':x.get('presentazione_ue'),
                             'AIC':x.get('aic'),'esito':x.get('esito_controllo_automatico'),
                             'motivi':', '.join(x.get('motivi',[]))}
                            for x in audit_doc.get('coppie',[])],height=320)
        except (OSError,ValueError,KeyError,TypeError):
            st.warning('Il rapporto di audit Gazzetta non è leggibile.')
    else:
        st.info('L’audit dettagliato delle coppie EU–AIC comparirà dopo il prossimo '
                'aggiornamento Gazzetta completato e verificato.')
    if not exists(db,'gazzetta_atti'):
        st.info('Il database selezionato non contiene ancora gli atti della Gazzetta. Esegui `importa_gazzetta.py` e `integra_allegati.py` sullo stesso archivio.')
    else:
        acts=query(db,'''SELECT codice,data_pubblicazione,titolo,tipo,completezza,
            numero_aic_estratte,url_ufficiale,testo_file,nominativi_campione
            FROM gazzetta_atti ORDER BY data_pubblicazione DESC,codice DESC''')
        show_table([{k:v for k,v in x.items() if k not in ('testo_file','nominativi_campione')} for x in acts],height=300)
        if acts:
            idx=st.selectbox('Esamina un provvedimento',range(len(acts)),
                            format_func=lambda i:f"{acts[i]['data_pubblicazione']} · {acts[i]['codice']} · {(acts[i]['titolo'] or '')[:55]}")
            act=acts[idx]
            st.markdown(f"#### Atto {act['codice']}")
            st.caption('La data mostrata è quella di pubblicazione, non necessariamente quella di efficacia.')
            official=safe_url(act.get('url_ufficiale'))
            if official: st.link_button('Apri documento ufficiale',official)
            if exists(db,'gu_coppie_eu_aic'):
                pairs=query(db,'SELECT eu,aic,stato,estratto,url_allegato FROM gu_coppie_eu_aic WHERE codice=? ORDER BY aic',(act['codice'],))
                st.markdown('**Coppie EU–AIC citate negli allegati**')
                show_table(pairs,height=220)
                st.caption('Un’estrazione automatica da documento richiede ancora controlli per dichiarare un collegamento verificato.')
            with st.expander('Testo estratto dal documento (potrebbe essere incompleto)'):
                p=Path(act.get('testo_file') or '')
                if p and str(p)!='.' and ((db.parent/p) if not p.is_absolute() else p).is_file():
                    actual=(db.parent/p) if not p.is_absolute() else p
                    text=actual.read_text(encoding='utf-8',errors='replace')
                    st.text_area('Testo GU',text[:18000],height=330,disabled=True)
                    if len(text)>18000: st.caption('È visualizzata soltanto la prima parte del testo.')
                else:
                    st.warning('File del testo non disponibile nella cartella locale. Verifica che la cartella risultati/gazzetta_testi sia presente.')

with qual_tab:
    st.subheader('Controlli di qualità e report per il relatore')
    qa_file=DATA/'qualita_strutturale.json'
    if qa_file.is_file():
        try:
            qa_report=json.loads(qa_file.read_text(encoding='utf-8'))
            state=qa_report.get('stato','NON VERIFICATO')
            st.markdown('#### Audit strutturale indipendente')
            st.metric('Integrità e provenienza',state)
            st.caption('Rapporto generato il: '+str(qa_report.get('generato_utc','non disponibile'))
                       +' · Ultimo controllo salvato, non esecuzione in tempo reale.')
            problems=qa_report.get('controlli_bloccanti',[])
            warnings=qa_report.get('avvertenze',[])
            if problems:st.error('Controlli da correggere: '+'; '.join(problems))
            if warnings:
                with st.expander('Avvertenze e dati non ancora completi'):
                    for warning in warnings:st.write('• '+warning)
            st.caption('Un audit strutturale superato non certifica la classe di rimborso corrente.')
        except (ValueError,OSError,TypeError):
            st.warning('Il rapporto strutturale non è leggibile.')
    else:
        st.info('Il rapporto strutturale sarà visibile dopo la prima esecuzione riuscita '
                'di «RegulaMed - verifica qualita database» in GitHub Actions.')

    st.caption('Tutti i numeri sono calcolati dal database scelto, non sono valori dimostrativi hardcoded.')
    qa=[{'Indicatore':'Integrità SQLite','Valore':'OK' if report['integrita']['quick_check'] else 'ERRORE'},
        {'Indicatore':'Errori FK','Valore':report['integrita']['errori_foreign_key']},
        {'Indicatore':'Link AIC senza confezione nel campione','Valore':coverage['link_orfani_aifa']},
        {'Indicatore':'Link EMA senza medicinale nel campione','Valore':coverage['link_orfani_ema']},
        {'Indicatore':'AIC candidate per più medicinali','Valore':coverage['aic_ambigue_tra_candidati']},
        {'Indicatore':'Medicinali EMA senza data della decisione','Valore':coverage['ema_senza_data_decisione']},
        {'Indicatore':'Medicinali EMA senza testo indicazioni','Valore':coverage['ema_senza_indicazione']},
        {'Indicatore':'Coppie GU senza link registrato','Valore':evidences['coppie_senza_link_precedente']},
        {'Indicatore':'Coppie GU in conflitto','Valore':evidences['coppie_in_conflitto']},]
    show_table(qa,height=360)
    st.markdown('#### Esporta il primo riscontro')
    left,right=st.columns(2)
    left.download_button('Scarica relazione per il relatore (.md)',
        relazione_md(report).encode('utf8'),'relazione_primo_riscontro.md','text/markdown',use_container_width=True)
    right.download_button('Scarica inventario medicinali (.csv)',
        csv_protetto(catalog),'inventario_medicinali.csv','text/csv',use_container_width=True)
    st.download_button('Scarica relazione impaginata (.html)', relazione_html(report).encode('utf8'),
                       'relazione_primo_riscontro.html', 'text/html')
    st.download_button('Scarica audit strutturato (.json)',json.dumps(report,ensure_ascii=False,indent=2),
                       'qualita_database.json','application/json')
    st.markdown('#### Limiti da dichiarare')
    for caveat in report['avvertenze']:
        st.write('• '+caveat)

with src_tab:
    st.subheader('Provenienza, aggiornamenti e versioni')
    if last:
        a,b,c=st.columns(3)
        a.metric('Tipo importazione',last.get('modalita') or 'N/D')
        b.metric('Importazione completata (UTC)',last.get('fine_utc') or 'N/D')
        c.metric('A/H: data di riferimento',formatta_data_italiana(last.get('data_liste_ah')))
        st.caption('Data di pubblicazione degli elenchi A/H: '
                   + (stato['pubblicazione_ah'] if stato else 'non verificata')
                   + '. Il giorno del download non coincide necessariamente con la pubblicazione '
                     'né con l’efficacia normativa degli atti.')
    st.markdown('#### Acquisizioni archiviate')
    acqu=[{k:r.get(k) for k in ('tipo','origine','data_acquisizione','data_riferimento','etichetta','sha256')}
          for r in report['acquisizioni']]
    show_table(acqu,height=340)
    st.markdown('#### Snapshot A/H archiviati')
    show_table(report['snapshot_ah'],height=220)
    st.info('EMA, Gazzetta e i tre componenti AIFA (anagrafica, A/H, C(nn)) si aggiornano autonomamente. I rapporti di successo sono separati. La Gazzetta conserva evidenze documentali da revisionare; A/H e C(nn) sono snapshot storici, non prove della classe vigente.')
    st.warning('Le acquisizioni ripetute possono conservare più record di provenienza per dataset. La data di download non coincide con la data di validità normativa.')
    st.markdown('#### Fonti istituzionali')
    st.markdown('• [EMA – Download medicine data](https://www.ema.europa.eu/en/medicines/download-medicine-data)')
    st.markdown('• [AIFA – Liste dei farmaci](https://www.aifa.gov.it/it/liste-dei-farmaci)')
    st.markdown('• [Gazzetta Ufficiale](https://www.gazzettaufficiale.it/)')
    st.markdown('• [TrovaNormeFarmaco](https://trovanorme.aifa.gov.it/)')

st.divider()
st.caption('RegulaMed · Prototipo accademico per l’integrazione documentale · Non fornisce consigli terapeutici, non certifica rimborsabilità corrente e non sostituisce le fonti istituzionali.')
