import tempfile
import unittest
from pathlib import Path
from aifa_principi_attivi import principi_per_aic,arricchisci_confezioni

class PrincipiAifaTest(unittest.TestCase):
    def test_join_by_aic_and_multiple_actives(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'PA_confezioni.csv'
            path.write_text('CODICE_AIC;PRINCIPIO_ATTIVO\n051960019;Lecanemab\n001234567;Sostanza B\n001234567;Sostanza A\n001234567;Sostanza A\n',encoding='utf-8')
            active,rows=principi_per_aic(path)
            self.assertEqual(rows,4)
            self.assertEqual(active['001234567'],'Sostanza A; Sostanza B')
            packs={'051960019':{'principio_attivo':''},
                   '001234567':{'principio_attivo':''}}
            stats=arricchisci_confezioni(packs,active)
            self.assertEqual(stats['aic_con_principio'],2)
            self.assertEqual(packs['051960019']['principio_attivo'],'Lecanemab')
    def test_reject_unexpected_columns(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'unexpected.csv'
            path.write_text('AIC;NOME\n051960019;Test\n',encoding='utf-8')
            with self.assertRaises(ValueError):
                principi_per_aic(path)
    def test_reject_low_coverage(self):
        with self.assertRaises(ValueError):
            arricchisci_confezioni({'001234567':{},'001234568':{}},{'001234567':'A'})
if __name__=='__main__':unittest.main()
