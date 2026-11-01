#!/usr/bin/env python3
"""Test di regressione locale, senza rete, per le evidenze GU.
Eseguire: python -m unittest -v test_gazzetta_incrementale.py
"""
import unittest
from unittest.mock import patch
import gazzetta_incrementale as gu


class GazzettaTests(unittest.TestCase):
    def test_well_known_cases_eu_aic(self):
        cases = [
            ('Leqembi', 'EU/1/24/1891/001 AIC:051960019 /E In base 32: 1KKQ6M',
             ('EU/1/24/1891/001', '051960019')),
            ('Kisunla', 'EU/1/25/1926/001 A.I.C.: 052446010 /E In base 32: 1L0JTU',
             ('EU/1/25/1926/001', '052446010')),
        ]
        for name, text, expected in cases:
            with self.subTest(name=name):
                self.assertIn(expected, gu.coppie_eu_aic_da_testo(text))

    def test_wegovy_aic_extract(self):
        text = "Wegovy. A.I.C. n. 049883061/E (in base 10) Classe di rimborsabilita' C"
        self.assertIn('049883061', gu.aic_evidences(text))

    def test_avoids_wrong_eu_aic_pair(self):
        text = ("EU/1/24/1891/001: presentation details. "
                "EU/1/25/1926/002 A.I.C.: 052446022 /E")
        pairs = gu.coppie_eu_aic_da_testo(text)
        self.assertEqual(set(pairs), {('EU/1/25/1926/002', '052446022')})

    def test_no_truncation_after_hundred(self):
        items = {f'26A{i:05d}': ('2026-10-20', f'Determina relativa al medicinale altro{i}')
                 for i in range(150)}
        items['26A99999'] = ('2026-10-21', 'Riclassificazione del medicinale Wegovy')
        results = gu.atti_pertinenti(items, ['Wegovy'])
        self.assertEqual([r[0] for r in results], ['26A99999'])

    def test_unrelated_title_not_promoted(self):
        candidates = {'26A00001': ('2026-11-01', 'Determinazione relativa ad altra sostanza')}
        self.assertEqual(gu.atti_pertinenti(candidates, ['Wegovy']), [])

    def test_title_generic_then_text_scan(self):
        acts={
            '26A00011':('2026-10-29','Determina contenente medicinali autorizzati'),
            '26A00012':('2026-10-28','Riclassificazione Wegovy'),
            '26A00013':('2026-10-27','Provvedimento farmaceutico generale'),
        }
        queue,remaining,named=gu.queue_screening(acts,['Wegovy'],set(),set(),2)
        self.assertEqual(queue[0][0],'26A00012')
        self.assertEqual(len(queue),2)
        self.assertEqual(remaining,3)
        self.assertEqual(named,1)
        self.assertIn('Wegovy',gu.matcher_medicinali(['Wegovy'])('Farmaco Wegovy in confezione'))
        self.assertEqual(gu.matcher_medicinali(['Wegovy'])('Wegovyplus'),[])

    def test_screening_skips_already_examined(self):
        acts={'26A00011':('2026-10-29','Medicinale X'),
              '26A00012':('2026-10-28','Medicinale Wegovy')}
        queue,_,_=gu.queue_screening(acts,['Wegovy'],{'26A00012'},{'26A00011'},80)
        self.assertEqual(queue,[])

    def test_reject_external_link(self):
        with self.assertRaises(ValueError):
            gu.safe('https://untrusted.example/serie_generale')

    def test_gu_article_code(self):
        self.assertEqual(
            gu.ident('https://www.gazzettaufficiale.it/eli/id/2026/05/06/26A02071/sg'),
            ('26A02071','2026-05-06'))


if __name__ == '__main__':
    unittest.main()
