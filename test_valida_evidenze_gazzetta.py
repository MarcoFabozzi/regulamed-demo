import json
import sqlite3
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from valida_evidenze_gazzetta import esamina


class AuditTests(unittest.TestCase):
    def test_consistent_document_and_conflict_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'gazzetta_testi').mkdir()
            text='Confezioni autorizzate: EU/1/21/1608/013 A.I.C.: 049883135; EU/1/21/1608/014 A.I.C.: 049883147.'
            (root/'gazzetta_testi'/'allegato.txt').write_text(text,encoding='utf-8')
            db=root/'sample.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('''
                CREATE TABLE gazzetta_atti(codice TEXT, url_ufficiale TEXT, data_pubblicazione TEXT);
                CREATE TABLE gu_allegati(codice TEXT,url TEXT,testo_file TEXT,sha256 TEXT);
                CREATE TABLE gu_coppie_eu_aic(codice TEXT,eu TEXT,aic TEXT,stato TEXT,url_allegato TEXT);
                CREATE TABLE link_verificati(ema_id TEXT,aic TEXT,codice_presentazione_ue TEXT);
                ''')
                c.execute('INSERT INTO gazzetta_atti VALUES (?,?,?)',(
                    '26A05342','https://www.gazzettaufficiale.it/eli/id/2026/10/01/26A05342/sg','2026-10-01'))
                c.execute('INSERT INTO gu_allegati VALUES (?,?,?,?)',(
                    '26A05342','https://www.gazzettaufficiale.it/atto/a',
                    'gazzetta_testi/allegato.txt',sha256(text.encode()).hexdigest()))
                c.execute('INSERT INTO gu_coppie_eu_aic VALUES (?,?,?,?,?)',(
                    '26A05342','EU/1/21/1608/013','049883135',
                    'EVIDENZA_DOCUMENTALE_DA_REVISIONARE','https://www.gazzettaufficiale.it/atto/a'))
            good=esamina(db)
            self.assertEqual(good['riepilogo']['COERENTE_NEL_TESTO_DA_REVISIONARE'],1)
            self.assertEqual(good['riepilogo']['ANOMALIA_DOCUMENTALE'],0)
            with sqlite3.connect(db) as c:
                c.execute('INSERT INTO link_verificati VALUES (?,?,?)',(
                    'EMEA/H/C/005422','049883999','EU/1/21/1608/013'))
            conflict=esamina(db)
            self.assertEqual(conflict['riepilogo']['REVISIONE_PRIORITARIA'],1)
            (root/'gazzetta_testi'/'allegato.txt').write_text(text+' changed',encoding='utf-8')
            damaged=esamina(db)
            self.assertEqual(damaged['riepilogo']['ANOMALIA_DOCUMENTALE'],1)


if __name__=='__main__':
    unittest.main()
