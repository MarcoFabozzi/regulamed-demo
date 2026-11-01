import sqlite3
import tempfile
import unittest
from pathlib import Path
from prepara_revisione_collegamenti import prepara

class ReportTests(unittest.TestCase):
    def test_name_match_is_never_verified(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.sqlite'
            with sqlite3.connect(p) as db:
                db.executescript("""
                CREATE TABLE gu_coppie_eu_aic(codice TEXT,eu TEXT,aic TEXT,stato TEXT);
                CREATE TABLE gazzetta_atti(codice TEXT,data_pubblicazione TEXT,url_ufficiale TEXT);
                CREATE TABLE aifa_confezioni(aic TEXT,nome TEXT,principio_attivo TEXT,descrizione TEXT);
                CREATE TABLE link_verificati(aic TEXT,codice_presentazione_ue TEXT,ema_id TEXT);
                CREATE TABLE ema_medicinali(ema_id TEXT,nome TEXT,principio_attivo TEXT);
                INSERT INTO gu_coppie_eu_aic VALUES('26A00001','EU/1/25/1926/001','052446010','EVIDENZA_DOCUMENTALE_DA_REVISIONARE');
                INSERT INTO gazzetta_atti VALUES('26A00001','2026-01-01','https://www.gazzettaufficiale.it/');
                INSERT INTO aifa_confezioni VALUES('052446010','Kisunla','Donanemab','Flaconcino');
                INSERT INTO ema_medicinali VALUES('EMEA/H/C/006001','Kisunla','Donanemab');
                """)
            r=prepara(p)
            self.assertEqual(r['totale_evidenze'],1)
            self.assertEqual(r['elementi'][0]['stato_revisione'],'CANDIDATO_NOME_EMA_DA_REVISIONARE')
            self.assertIsNone(r['elementi'][0]['ema_documentato'])
if __name__=='__main__':unittest.main()
