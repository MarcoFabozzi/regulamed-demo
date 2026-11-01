#!/usr/bin/env python3
"""RegulaMed: dashboard essenziale in sola lettura (tesi triennale)."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import re
from html import escape
from urllib.parse import urlsplit
import sqlite3
import tempfile

import streamlit as st

BASE=Path(__file__).resolve().parent
DATA=BASE/'risultati'
DB=DATA/'farmaci_automatico.sqlite'
AIFA=DATA/'aifa_confezioni_complete.sqlite.gz'

st.set_page_config(page_title='RegulaMed',page_icon='💊',layout='centered')
st.markdown("""
<style>
.stApp {background: linear-gradient(180deg, #e9f8f5 0, #ffffff 350px);}
.block-container {padding-top: 1.5rem; max-width: 980px;}
.regula-hero {background:linear-gradient(115deg,#103d55 0%,#147d76 75%,#1a9c87 100%);
              color:white;padding:26px 30px;border-radius:22px;
              box-shadow:0 12px 30px rgba(17,71,81,.13);}
.regula-hero .eyebrow {font-size:.76rem;letter-spacing:.09em;color:#c2f4e4;font-weight:700;}
.regula-hero h1 {font-size:2.25rem;line-height:1.2;margin:9px 0 7px;color:#fff;}
.regula-hero p {margin:0;color:#e6f6f5;font-size:.95rem;}
h3 {color:#16475a!important;margin-top:1.3rem!important;}
div[data-testid="stMetric"] {background:linear-gradient(155deg,#ffffff,#e4f7f1);
    border:1px solid #c6e9df;padding:12px 14px;border-radius:15px;
    box-shadow:0 3px 12px rgba(10,88,86,.06);}
div[data-testid="stMetricValue"] {color:#0c716d;font-weight:750;}
div[data-testid="stMetricLabel"] {color:#315c68;}
div[data-testid="stTextInput"] input {background:#f5fbf9;border-radius:10px;}
div[data-testid="stButton"] button {border-radius:11px;border:1px solid #cce8e2;
    background:#f4fbf9;color:#164c5c;font-weight:550;}
div[data-testid="stButton"] button:hover {background:#dff5ee;border-color:#169b85;color:#105c5b;}

.regula-packages-scroll {max-height:410px;overflow-y:auto;overflow-x:hidden;
  border:1px solid #cce8e2;border-radius:13px;background:white;}
.regula-packages {border-collapse:collapse;width:100%;table-layout:fixed;
  font-size:.84rem;color:#163b4a;}
.regula-packages th {position:sticky;top:0;z-index:1;background:#e5f5f0;
  color:#185a58;text-align:left;padding:11px 9px;border-bottom:2px solid #cce8e2;}
.regula-packages td {padding:11px 9px;vertical-align:top;line-height:1.45;
  border-bottom:1px solid #e5efeb;white-space:normal;overflow-wrap:anywhere;}
.regula-packages tr:nth-child(even) {background:#f5fbf9;}
.regula-packages th:first-child,.regula-packages td:first-child {width:15%;}
.regula-packages th:nth-child(2),.regula-packages td:nth-child(2) {width:22%;}
.regula-packages th:nth-child(3),.regula-packages td:nth-child(3) {width:39%;}
.regula-packages th:nth-child(4),.regula-packages td:nth-child(4) {width:10%;}
.regula-packages th:nth-child(5),.regula-packages td:nth-child(5) {width:14%;}
.regula-packages details summary {cursor:pointer;color:#0c716d;font-weight:650;}
.regula-packages .regula-more {margin-top:8px;padding:8px;background:#eef9f5;
  border-radius:8px;line-height:1.75;overflow-wrap:anywhere;}
.regula-packages a {color:#08766f;text-decoration:underline;}
.regula-detail-link {display:inline-block;color:#086f72!important;font-weight:700;
  text-decoration:underline!important;overflow-wrap:break-word;}
.regula-detail-link:hover {color:#0c9e8b!important;}


@media(max-width:650px) {
  .regula-packages {font-size:.76rem;}
  .regula-packages th,.regula-packages td {padding:9px 6px;}
}


@media(max-width:650px) {
    .regula-hero {padding:20px 18px;border-radius:17px;}
    .regula-hero h1 {font-size:1.85rem;}
    div[data-testid="stMetric"] {padding:8px 7px;}
}
</style>
<div class="regula-hero">
<div class="eyebrow">TESI TRIENNALE · ARCHIVIO FARMACI</div>
<h1>💊 RegulaMed</h1>
<p>Ricerca medicinali e confezioni dalle fonti ufficiali EMA, AIFA e Gazzetta Ufficiale.</p>
</div>
""",unsafe_allow_html=True)

if not DB.is_file():
    st.error('Database non disponibile. Controllare il file risultati/farmaci_automatico.sqlite.')
    st.stop()


def query(sql,params=()):
    with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        return [dict(x) for x in con.execute(sql,params)]


def ultimo(fonte):
    path=DATA/'stato_fonti'/(fonte+'.json')
    try:
        obj=json.loads(path.read_text(encoding='utf-8'))
        if obj.get('esito')!='OK':return 'Non disponibile'
        dt=datetime.fromisoformat(obj['ultimo_successo_utc'].replace('Z','+00:00'))
        return dt.astimezone(ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y %H:%M')
    except (OSError,KeyError,ValueError,TypeError):
        return 'Non disponibile'


@st.cache_resource(show_spinner=False)
def archivio_aifa(path,version):
    from aifa_archivio_completo import estrai_archivio
    temp=Path(tempfile.mkdtemp(prefix='regulamed_aifa_'))/'aifa.sqlite'
    state=DATA/'stato_fonti'/'aifa_anagrafica.json'
    expected=None
    if state.is_file():
        obj=json.loads(state.read_text(encoding='utf-8'))
        expected=obj.get('statistiche',{}).get('archivio_completo',{}).get('sha256_archivio')
    estrai_archivio(path,temp,expected_sha256=expected)
    return temp


def scheda_confezione(aic):
    """Scheda AIFA consultabile mediante ?aic=XXXXXXXXX; solo query in lettura."""
    st.markdown('### 📋 Scheda della confezione')
    if st.button('← Torna all’elenco dei farmaci',key='back_to_catalog'):
        st.query_params.clear()
        st.rerun()
    if not re.fullmatch(r'\d{9}',str(aic)):
        st.error('Codice AIC non valido.')
        st.stop()

    package=None
    archive_source=False
    if AIFA.is_file():
        try:
            archive=archivio_aifa(str(AIFA),AIFA.stat().st_mtime_ns)
            with closing(sqlite3.connect(archive.as_uri()+'?mode=ro',uri=True)) as con:
                con.row_factory=sqlite3.Row
                con.execute('PRAGMA query_only=ON')
                row=con.execute('SELECT aic,nome,principio_attivo,descrizione,'
                    'stato_amministrativo,classe_da_anagrafica FROM confezioni WHERE aic=?',
                    (aic,)).fetchone()
                if row:
                    package=dict(row)
                    archive_source=True
        except (OSError,ValueError,sqlite3.DatabaseError):
            st.warning('Archivio AIFA completo non leggibile. Consulto i dati principali.')

    if package is None:
        records=query('SELECT aic,nome,principio_attivo,descrizione,'
                      'stato_amministrativo,classe_da_anagrafica '
                      'FROM aifa_confezioni WHERE aic=?',(aic,))
        if records:
            package=records[0]
    if not package:
        st.warning('Non risulta alcuna confezione con questo codice AIC nel database acquisito.')
        st.stop()

    st.subheader(package['nome'])
    st.caption('Dati della confezione AIFA · AIC '+package['aic'])
    st.markdown('#### Dati della confezione')
    st.write('**Codice AIC:**',package['aic'])
    st.write('**Denominazione:**',package['nome'])
    st.write('**Principio attivo:**',package['principio_attivo'] or 'Non disponibile nel file acquisito')
    st.write('**Descrizione completa:**')
    st.write(package['descrizione'] or 'Non disponibile nel file acquisito')
    st.write('**Stato amministrativo nel file:**',
             package['stato_amministrativo'] or 'Non disponibile')

    classes=query('SELECT classe,data_riferimento FROM classi_snapshot '
                  'WHERE aic=? ORDER BY data_riferimento DESC,classe',(aic,))
    registry_class=str(package['classe_da_anagrafica'] or '').strip()
    st.markdown('#### Classe e riferimenti temporali')
    st.write('**Classe riportata nell’anagrafica AIFA:**',registry_class or 'Non disponibile')
    if classes:
        st.write('**Classificazioni A/H conservate (storiche):**')
        for c in classes:
            st.write(f"{c['classe']} — riferimento {c['data_riferimento']}")
    else:
        st.write('**Elenchi A/H:** nessuna classificazione storica presente per questa AIC.')
    st.caption('I dati mostrati non certificano la classe o la rimborsabilità attualmente vigente. '
               'L’assenza di una classe non implica automaticamente la classe C.')

    documented=query('SELECT l.ema_id,e.nome,l.codice_presentazione_ue,'
                     'l.metodo,l.fonte_provvedimento FROM link_verificati l '
                     'LEFT JOIN ema_medicinali e ON e.ema_id=l.ema_id '
                     'WHERE l.aic=? ORDER BY e.nome',(aic,))
    candidates=query('SELECT m.ema_id,e.nome,m.criterio,m.stato '
                     'FROM match_candidati m '
                     'LEFT JOIN ema_medicinali e ON e.ema_id=m.ema_id '
                     'WHERE m.aic=? ORDER BY e.nome LIMIT 20',(aic,))
    st.markdown('#### Collegamenti con EMA')
    if documented:
        for item in documented:
            st.write(f"**{item['nome'] or item['ema_id']}** — {item['ema_id']}")
            st.write('Codice presentazione UE:',item['codice_presentazione_ue'])
            st.caption('Collegamento documentato nel progetto.')
            source=item['fonte_provvedimento'] or ''
            u=urlsplit(source)
            if u.scheme=='https' and u.hostname in {
                'www.gazzettaufficiale.it','www.ema.europa.eu','www.aifa.gov.it'
            }:
                st.link_button('Apri il documento di riferimento',source)
    elif candidates:
        st.info('Sono disponibili soltanto corrispondenze candidate, non verificate.')
        st.dataframe([{
            'Medicinale EMA':x['nome'] or x['ema_id'],
            'Identificativo EMA':x['ema_id'],
            'Criterio proposto':x['criterio']
        } for x in candidates],hide_index=True,use_container_width=True)
    else:
        st.write('Non risultano collegamenti EMA documentati per questa confezione.')

    evidence=query('SELECT g.codice,g.titolo,g.data_pubblicazione,g.url_ufficiale,'
                   'v.snippet,v.ipotesi_classe '
                   'FROM gazzetta_evidenze_aic v JOIN gazzetta_atti g ON g.codice=v.codice '
                   'WHERE v.aic=? ORDER BY g.data_pubblicazione DESC LIMIT 20',(aic,))
    pairs=query('SELECT p.codice,p.eu,g.url_ufficiale,g.data_pubblicazione '
                'FROM gu_coppie_eu_aic p JOIN gazzetta_atti g ON g.codice=p.codice '
                'WHERE p.aic=? ORDER BY g.data_pubblicazione DESC LIMIT 20',(aic,))
    st.markdown('#### Gazzetta Ufficiale')
    if evidence or pairs:
        seen_codes=set()
        for item in evidence:
            if item['codice'] not in seen_codes:
                st.write(f"**{item['codice']}** — {item['data_pubblicazione']}")
                st.write(item['titolo'])
                if item['url_ufficiale'].startswith('https://www.gazzettaufficiale.it/'):
                    st.link_button('Consulta l’atto ufficiale',item['url_ufficiale'])
                seen_codes.add(item['codice'])
        if pairs:
            st.caption('Codici UE–AIC individuati automaticamente negli allegati; '
                       'non costituiscono collegamenti normativi confermati.')
            st.dataframe([{'Atto':x['codice'],'Presentazione UE':x['eu']}
                         for x in pairs],hide_index=True,use_container_width=True)
    else:
        st.write('Nessuna evidenza Gazzetta collegata a questa AIC nel database attuale.')

    st.markdown('#### Fonte e aggiornamento')
    st.write('**Origine della scheda:**', 'Archivio nazionale AIFA completo' if archive_source
             else 'Database principale RegulaMed')
    st.write('**Ultima acquisizione riuscita dell’anagrafica AIFA:**',ultimo('aifa_anagrafica'))
    st.link_button('Consulta le liste ufficiali AIFA',
                   'https://www.aifa.gov.it/liste-dei-farmaci')
    st.caption('Prototipo accademico in sola lettura, non sostituisce le fonti ufficiali.')


requested_aic=st.query_params.get('aic')
if requested_aic is not None:
    scheda_confezione(requested_aic)
    st.stop()


ema=query('SELECT COUNT(*) AS n FROM ema_medicinali')[0]['n']
gu=query('SELECT COUNT(*) AS n FROM gazzetta_atti')[0]['n']
aifa_main=query('SELECT COUNT(*) AS n FROM aifa_confezioni')[0]['n']
aifa_total=aifa_main
aifa_label='Confezioni AIFA nel DB'
if AIFA.is_file():
    state=DATA/'stato_fonti'/'aifa_anagrafica.json'
    try:
        doc=json.loads(state.read_text(encoding='utf-8'))
        n=int(doc['statistiche']['archivio_completo']['confezioni_totali'])
        if doc.get('esito')=='OK' and n>=aifa_main:
            aifa_total=n
            aifa_label='Confezioni AIFA'
    except (OSError,KeyError,TypeError,ValueError):
        pass
st.markdown('### 📊 Archivio disponibile')
c1,c2,c3=st.columns(3)
c1.metric('Medicinali EMA',f'{ema:,}'.replace(',','.'))
c2.metric(aifa_label,f'{aifa_total:,}'.replace(',','.'))
c3.metric('Atti Gazzetta',gu)

st.markdown('### 🔎 Cerca un medicinale')
term=st.text_input('Nome, principio attivo, codice EMA o AIC',placeholder='Es. Tachipirina, Cardirene, Leqembi')
if term.strip():
    pattern='%'+term.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    medicines=query("""SELECT ema_id,nome,principio_attivo,stato_ema,data_decisione_ue,url_ema,indicazione_ema
        FROM ema_medicinali
        WHERE nome LIKE ? ESCAPE '\\' OR principio_attivo LIKE ? ESCAPE '\\'
           OR ema_id LIKE ? ESCAPE '\\'
        ORDER BY nome LIMIT 80""",(pattern,pattern,pattern))
    aic_matches=query("""SELECT DISTINCT e.ema_id,e.nome,e.principio_attivo,e.stato_ema,
           e.data_decisione_ue,e.url_ema,e.indicazione_ema
        FROM ema_medicinali e JOIN link_verificati l ON e.ema_id=l.ema_id
        WHERE l.aic LIKE ? ESCAPE '\\' LIMIT 30""",(pattern,))
    found={x['ema_id']:x for x in medicines+aic_matches}
    matches=sorted(found.values(),key=lambda x:x['nome'].casefold())
    if matches:
        options={f"{x['nome']} · {x['ema_id']}":x for x in matches}
        chosen=st.selectbox('Medicinale EMA',list(options))
        med=options[chosen]
        st.markdown(f"**{med['nome']}**")
        st.write('Principio attivo:',med.get('principio_attivo') or 'Non disponibile')
        st.write('Codice EMA:',med['ema_id'])
        st.write('Stato EMA:',med.get('stato_ema') or 'Non disponibile')
        st.write('Data decisione UE nel dataset:',med.get('data_decisione_ue') or 'Non disponibile')
        if med.get('url_ema') and med['url_ema'].startswith('https://www.ema.europa.eu/'):
            st.link_button('Scheda ufficiale EMA',med['url_ema'])
        with st.expander('Indicazione terapeutica EMA'):
            st.write(med.get('indicazione_ema') or 'Non disponibile')
        links=query("""SELECT l.aic,a.descrizione,l.codice_presentazione_ue,l.fonte_provvedimento
            FROM link_verificati l LEFT JOIN aifa_confezioni a ON a.aic=l.aic
            WHERE l.ema_id=? ORDER BY l.aic""",(med['ema_id'],))
        if links:
            st.markdown('**Confezioni con collegamento documentato**')
            st.dataframe(links,hide_index=True,use_container_width=True)
        else:
            st.info('Nessuna confezione con collegamento documentato per questo medicinale.')
        candidates=query("""SELECT m.aic,a.descrizione,m.criterio
            FROM match_candidati m LEFT JOIN aifa_confezioni a ON a.aic=m.aic
            WHERE m.ema_id=? ORDER BY m.aic LIMIT 100""",(med['ema_id'],))
        if candidates:
            with st.expander(f'Possibili associazioni AIFA da verificare ({len(candidates)} mostrate)'):
                st.dataframe(candidates,hide_index=True,use_container_width=True)
    else:
        st.info('Nessuna scheda nel catalogo EMA importato. Il medicinale potrebbe essere autorizzato a livello nazionale: consulta i risultati AIFA sotto.')

    if AIFA.is_file():
        from aifa_archivio_completo import ricerca_confezioni
        from collegamenti_aifa import annota_collegamenti
        try:
            file_aifa=archivio_aifa(str(AIFA),AIFA.stat().st_mtime_ns)
            packages=annota_collegamenti(DB,ricerca_confezioni(file_aifa,term,limit=50))
            st.markdown('#### Confezioni AIFA - archivio nazionale')
            st.caption('Risultati disponibili anche per medicinali senza scheda nel catalogo EMA.')
            if packages:
                for item in packages:
                    if not str(item.get('principio_attivo') or '').strip():
                        item['principio_attivo']='Non disponibile nel file acquisito'
                st.dataframe(packages,hide_index=True,use_container_width=True)
                st.caption('Se il principio attivo manca, non significa che il farmaco ne sia privo. '
                           'Serve completare l’importazione del file ufficiale con i principi attivi.')
            else:
                st.info('Nessuna confezione trovata nell’ultimo archivio AIFA disponibile.')
        except (OSError,ValueError,sqlite3.DatabaseError) as exc:
            st.warning(f'Archivio AIFA momentaneamente non disponibile: {exc}')

st.markdown('### 📚 Elenco dei farmaci')
st.caption('Scorri l’elenco e tocca una riga per vedere le confezioni. Puoi filtrare per lettera.')

if AIFA.is_file():
    try:
        list_db=archivio_aifa(str(AIFA),AIFA.stat().st_mtime_ns)
    except (OSError,ValueError,sqlite3.DatabaseError) as exc:
        st.warning(f'Archivio AIFA completo non accessibile: {exc}')
        list_db=DB
        source_table='aifa_confezioni'
    else:
        source_table='confezioni'
else:
    list_db=DB
    source_table='aifa_confezioni'

letter=st.selectbox('Filtra per iniziale',['Tutte']+list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'),
                    key='aifa_letter')
filter_id=(term.strip().casefold(),letter,str(list_db),list_db.stat().st_mtime_ns)
if st.session_state.get('aifa_filter_id')!=filter_id:
    st.session_state['aifa_filter_id']=filter_id
    st.session_state['aifa_selected_row']=None

conditions=["nome IS NOT NULL","TRIM(nome) <> ''"]
params=[]
if term.strip():
    raw=term.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    pattern='%'+raw+'%'
    conditions.append("(nome LIKE ? ESCAPE '\\' OR aic LIKE ? ESCAPE '\\' OR principio_attivo LIKE ? ESCAPE '\\')")
    params.extend((pattern,pattern,pattern))
if letter!='Tutte':
    conditions.append("nome LIKE ?")
    params.append(letter+'%')

@st.cache_data(show_spinner=False,max_entries=24)
def elenco_nomi(db_file,table,where,values,version):
    sql=("SELECT DISTINCT nome FROM "+table+" WHERE "+where+
         " ORDER BY nome COLLATE NOCASE")
    with closing(sqlite3.connect(Path(db_file).as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        return [row[0] for row in conn.execute(sql,values)]

try:
    names=elenco_nomi(str(list_db),source_table,' AND '.join(conditions),
                      tuple(params),list_db.stat().st_mtime_ns)
    if names:
        st.caption(f'{len(names):,} denominazioni disponibili'.replace(',','.'))
        # Il riquadro rimane scorrevole senza creare migliaia di pulsanti.
        selection=st.dataframe(
            [{'Medicinale':name} for name in names],
            hide_index=True,use_container_width=True,height=370,
            on_select='rerun',selection_mode='single-row',
            key='aifa_scroll_list'
        )
        selected_rows=selection.selection.rows
        if selected_rows:
            name=names[selected_rows[0]]
            with closing(sqlite3.connect(list_db.as_uri()+'?mode=ro',uri=True)) as conn:
                conn.row_factory=sqlite3.Row
                conn.execute('PRAGMA query_only=ON')
                rows=[dict(r) for r in conn.execute(
                    "SELECT aic,nome,principio_attivo,descrizione,classe_da_anagrafica,stato_amministrativo FROM "+source_table+
                    " WHERE nome=? ORDER BY aic LIMIT 101",(name,))]
            st.markdown('#### Confezioni di '+name)
            st.caption('Le descrizioni lunghe continuano su più righe, nella stessa colonna.')
            # Gli snapshot A/H sono storici: non sovrascrivono la classe da anagrafica.
            ids=[str(r['aic']) for r in rows[:100]]
            history={}
            if ids:
                placeholders=','.join('?' for _ in ids)
                for record in query(
                    'SELECT aic,classe,MAX(data_riferimento) AS data_riferimento '
                    'FROM classi_snapshot WHERE aic IN ('+placeholders+') '
                    'GROUP BY aic,classe',ids):
                    history.setdefault(record['aic'],[]).append(
                        record['classe']+' ('+record['data_riferimento']+')')
            table_rows=[]
            for package in rows[:100]:
                aic=escape(str(package['aic'] or ''))
                active=escape(str(package['principio_attivo'] or 'Non disponibile'))
                description=escape(str(package['descrizione'] or 'Non disponibile'))
                registered_class=str(package['classe_da_anagrafica'] or '').strip()
                historic_classes=history.get(str(package['aic']),[])
                # Unico snapshot: utile come dato storico, MAI come classe corrente.
                historic_fallback=(historic_classes[0].split(' (')[0]+' (storica)'
                                   if len(historic_classes)==1 else 'N/D')
                cls=escape(registered_class or historic_fallback)
                admin=escape(str(package['stato_amministrativo'] or 'Non disponibile'))
                snapshots=escape(', '.join(historic_classes) or 'Nessuno')
                # Il tag HTML details e' espandibile senza altri clic o ricaricamenti.
                extra=(
                    '<a class="regula-detail-link" href="?aic='+aic+'" target="_self">'
                    '🔎 Apri scheda</a>'
                )
                table_rows.append(
                    f'<tr><td>{aic}</td><td>{active}</td><td>{description}</td>'
                    f'<td>{cls}</td><td>{extra}</td></tr>'
                )
            st.markdown(
                '<div class="regula-packages-scroll">'
                '<table class="regula-packages">'
                '<thead><tr><th>AIC</th><th>Principio attivo</th><th>Confezione</th>'
                '<th>Classe</th><th>Info</th></tr></thead>'
                '<tbody>'+''.join(table_rows)+'</tbody></table></div>',
                unsafe_allow_html=True
            )
            st.caption('La classe proviene dall’anagrafica AIFA, oppure è indicata come storica '
                       'quando disponibile soltanto in uno snapshot A/H. N/D = non disponibile. '
                       'La classificazione attuale e la rimborsabilità non sono certificate.')
            if len(rows)>100:
                st.caption('Visualizzate le prime 100 confezioni.')
        else:
            st.caption('Seleziona una riga dell’elenco per aprire le confezioni.')
    else:
        st.info('Nessun medicinale corrisponde ai filtri selezionati.')
except (sqlite3.DatabaseError,OSError,ValueError) as exc:
    st.warning(f'Impossibile visualizzare l’elenco: {exc}')

with st.expander('Stato degli aggiornamenti'):
    st.write('EMA:',ultimo('ema'))
    st.write('AIFA · confezioni:',ultimo('aifa_anagrafica'))
    st.write('AIFA · classi A/H:',ultimo('aifa_ah'))
    st.write('AIFA · storico C(nn):',ultimo('aifa_cnn'))
    st.write('Gazzetta Ufficiale:',ultimo('gazzetta'))
    st.caption('Gli aggiornamenti sono giornalieri, con nuovi tentativi in caso di errore. Le date riportano le acquisizioni, non la validità normativa.')
st.caption('Prototipo accademico: le informazioni non certificano la classificazione o la rimborsabilità corrente dei medicinali.')
