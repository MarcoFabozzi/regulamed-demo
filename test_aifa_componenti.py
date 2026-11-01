import unittest
from unittest.mock import patch
import aifa_componenti as aifa


class AIFAComponentsTests(unittest.TestCase):
    def test_anagrafica_uses_direct_known_official_address(self):
        self.assertEqual(aifa.ANAGRAFICA,
                         'https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv')

    def test_required_groups_are_independent(self):
        self.assertEqual(aifa.GROUPS['aifa_ah'], ('aifa_a', 'aifa_h'))
        self.assertEqual(aifa.GROUPS['aifa_cnn'], ('aifa_cnn',))

    def test_ah_requires_common_date(self):
        with self.assertRaises(ValueError):
            aifa.riferimento_ah({
                'aifa_a':'https://www.aifa.gov.it/documents/20142/3815901/Classe-A_per_nome-commerciale_30-6-2026.csv',
                'aifa_h':'https://www.aifa.gov.it/documents/20142/3815901/Classe-H_per_nome-commerciale_31-7-2026.csv'
            })

    def test_ah_accepts_common_date(self):
        self.assertEqual(aifa.riferimento_ah({
            'aifa_a':'https://www.aifa.gov.it/documents/20142/3815901/Classe-A_per_nome-commerciale_30-6-2026.csv',
            'aifa_h':'https://www.aifa.gov.it/documents/20142/3815901/Classe-H_per_nome-commerciale_30-6-2026.csv'
        }), '2026-06-30')

    def test_rejects_untrusted_source(self):
        with self.assertRaises(ValueError):
            aifa.assert_urls({'aifa_cnn':'https://example.org/lista_Cnn_2026.csv'}, ('aifa_cnn',))

    def test_cnn_date_filename(self):
        self.assertEqual(aifa._date_cnn_from_url(
            'https://www.aifa.gov.it/documents/20142/847358/lista_farmaci_valutati_inserimento_classe_Cnn_14.10.2026.csv'),
            '2026-10-14')


if __name__ == '__main__':
    unittest.main()
