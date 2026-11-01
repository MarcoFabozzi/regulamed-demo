import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aifa_archivio_completo import crea_archivio,estrai_archivio,ricerca_confezioni

def row(n):
    return {'aic':str(n).zfill(9),'nome':'Farmaco Test','principio_attivo':'Molecola',
            'descrizione':'Compresse','stato_amministrativo':'attivo','classe_da_anagrafica':'A'}

class ArchiveTests(unittest.TestCase):
    def test_create_extract_search(self):
        with tempfile.TemporaryDirectory() as t:
            src=Path(t)/'source.gz'
            result=crea_archivio((row(i) for i in range(100)),src,'a'*64,
                                  'https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv',min_rows=100)
            self.assertEqual(result['confezioni_totali'],100)
            dest=Path(t)/'confezioni.sqlite'
            with patch('aifa_archivio_completo.MIN_ROWS',100):
                self.assertEqual(estrai_archivio(src,dest,result['sha256_archivio']),100)
            self.assertEqual(len(ricerca_confezioni(dest,'Molecola')),100)
            self.assertEqual(len(ricerca_confezioni(dest,'000000042')),1)
            self.assertFalse(ricerca_confezioni(dest,'xx'))
    def test_wrong_hash_refused(self):
        with tempfile.TemporaryDirectory() as t:
            src=Path(t)/'source.gz'
            crea_archivio((row(i) for i in range(100)),src,'a'*64,
                          'https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv',min_rows=100)
            with self.assertRaises(ValueError):
                estrai_archivio(src,Path(t)/'out.sqlite','f'*64)
    def test_duplicate_aic_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                crea_archivio([row(1),row(1)],Path(t)/'a.gz','a'*64,'https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv',min_rows=2)

if __name__=='__main__':
    unittest.main()
