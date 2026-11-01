import sqlite3
import tempfile
import unittest
from pathlib import Path
from collegamenti_aifa import annota_collegamenti

class LinkLookupTests(unittest.TestCase):
    def test_documented_candidate_and_unlinked(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'test.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript("""
                CREATE TABLE link_verificati(ema_id TEXT,aic TEXT);
                CREATE TABLE match_candidati(ema_id TEXT,aic TEXT);
                INSERT INTO link_verificati VALUES('EMA-D','000000001');
                INSERT INTO match_candidati VALUES('EMA-C','000000002');
                INSERT INTO match_candidati VALUES('EMA-C2','000000002');
                """)
            entries=[{'aic':f'{i:09d}','nome':f'Med{i}'} for i in (1,2,3)]
            result=annota_collegamenti(path,entries)
            self.assertEqual(result[0]['stato_collegamento_ema'],'Documentato (nel database)')
            self.assertEqual(result[0]['codici_ema_documentati'],'EMA-D')
            self.assertEqual(result[1]['ema_candidati'],2)
            self.assertEqual(result[1]['stato_collegamento_ema'],'Candidato - da verificare')
            self.assertEqual(result[2]['stato_collegamento_ema'],'Nessun collegamento documentato')
            self.assertEqual(annota_collegamenti(path,[]),[])
if __name__=='__main__':unittest.main()
