#!/usr/bin/env python3
"""RegulaMed: dashboard essenziale in sola lettura (tesi triennale)."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sqlite3
import tempfile

import streamlit as st

BASE=Path(__file__).resolve().parent
DATA=BASE/'risultati'
DB=DATA/'farmaci_automatico.sqlite'
AIFA=DATA/'aifa_confezioni_complete.sqlite.gz'

st.set_page_config(page_title='RegulaMed',page_icon='💊',layout='centered')
st.title('💊 RegulaMed')
st.caption('Consultazione di dati EMA, AIFA e Gazzetta Ufficiale · Prototipo di tesi triennale')

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


ema=query('SELECT COUNT(*) AS n FROM ema_medicinali')[0]['n']
gu=query('SELECT COUNT(*) AS n FROM gazzetta_atti')[0]['n']
aifa_main=query('SELECT COUNT(*) AS n FROM aifa_confezioni')[0]['n']
st.markdown('### Archivio disponibile')
c1,c2,c3=st.columns(3)
c1.metric('Medicinali EMA',f'{ema:,}'.replace(',','.'))
c2.metric('Confezioni AIFA*',f'{aifa_main:,}'.replace(',','.'))
c3.metric('Atti Gazzetta',gu)
st.caption('* Confezioni collegate al database principale. L’anagrafica AIFA completa contiene oltre 160.000 confezioni.')

st.markdown('### Cerca un medicinale')
term=st.text_input('Nome, principio attivo, codice EMA o AIC',placeholder='Es. Leqembi, Wegovy, semaglutide')
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
        st.info('Nessun medicinale EMA trovato. Puoi comunque cercare nelle confezioni AIFA.')

    if AIFA.is_file():
        from aifa_archivio_completo import ricerca_confezioni
        from collegamenti_aifa import annota_collegamenti
        try:
            file_aifa=archivio_aifa(str(AIFA),AIFA.stat().st_mtime_ns)
            packages=annota_collegamenti(DB,ricerca_confezioni(file_aifa,term,limit=50))
            with st.expander(f'Risultati nell’anagrafica AIFA completa ({len(packages)} mostrati)',expanded=not matches):
                if packages:st.dataframe(packages,hide_index=True,use_container_width=True)
                else:st.write('Nessuna confezione trovata.')
        except (OSError,ValueError,sqlite3.DatabaseError) as exc:
            st.warning(f'Archivio AIFA momentaneamente non disponibile: {exc}')

st.markdown('### Provvedimenti della Gazzetta Ufficiale')
acts=query("""SELECT codice,data_pubblicazione,titolo,url_ufficiale
        FROM gazzetta_atti ORDER BY data_pubblicazione DESC,codice DESC LIMIT 100""")
if acts:
    choices={f"{x['data_pubblicazione']} · {x['codice']} · {(x['titolo'] or '')[:65]}":x for x in acts}
    label=st.selectbox('Scegli un provvedimento',list(choices))
    act=choices[label]
    st.write(act['titolo'])
    if (act['url_ufficiale'] or '').startswith('https://www.gazzettaufficiale.it/'):
        st.link_button('Leggi sulla Gazzetta Ufficiale',act['url_ufficiale'])
    pairs=query("""SELECT eu AS codice_ue,aic,stato FROM gu_coppie_eu_aic
        WHERE codice=? ORDER BY aic""",(act['codice'],))
    if pairs:
        st.caption('Coppie EU–AIC estratte automaticamente: da verificare, non certificazioni regolatorie.')
        st.dataframe(pairs,hide_index=True,use_container_width=True)
else:
    st.info('Nessun provvedimento acquisito.')

with st.expander('Stato degli aggiornamenti'):
    st.write('EMA:',ultimo('ema'))
    st.write('AIFA · confezioni:',ultimo('aifa_anagrafica'))
    st.write('AIFA · classi A/H:',ultimo('aifa_ah'))
    st.write('AIFA · storico C(nn):',ultimo('aifa_cnn'))
    st.write('Gazzetta Ufficiale:',ultimo('gazzetta'))
    st.caption('Gli aggiornamenti sono giornalieri, con nuovi tentativi in caso di errore. Le date riportano le acquisizioni, non la validità normativa.')
st.caption('Prototipo accademico: le informazioni non certificano la classificazione o la rimborsabilità corrente dei medicinali.')
