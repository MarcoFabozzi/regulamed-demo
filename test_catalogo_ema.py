import sqlite3
import unittest
from catalogo_ema import importa_catalogo_ema

def rec(n,status='Authorised'):
    return {'ema_id':f'EMEA/H/C/{n:06d}','nome':f'Medicine {n}','principio_attivo':'ingredient',
            'stato_ema':status,'data_decisione_ue':'2026-08-01','indicazione_ema':'test',
            'url_ema':'https://www.ema.europa.eu/en/medicines/human/test'}

class CatalogoTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE acquisizioni(id TEXT PRIMARY KEY,data_acquisizione TEXT);
        CREATE TABLE ema_medicinali(ema_id TEXT PRIMARY KEY,nome TEXT,principio_attivo TEXT,
          stato_ema TEXT,data_decisione_ue TEXT,indicazione_ema TEXT,url_ema TEXT,
          acquisizione_id TEXT REFERENCES acquisizioni(id));
        CREATE TABLE aifa_confezioni(aic TEXT PRIMARY KEY,nome TEXT);
        CREATE TABLE link_verificati(ema_id TEXT,aic TEXT,codice_presentazione_ue TEXT,
          PRIMARY KEY(ema_id,aic));
        INSERT INTO acquisizioni VALUES('old','2026-10-30T09:00:00+00:00');
        INSERT INTO acquisizioni VALUES('new','2026-11-01T09:00:00+00:00');
        """)
        for i in range(30):
            r=rec(i)
            self.db.execute('INSERT INTO ema_medicinali VALUES(?,?,?,?,?,?,?,?)',
                (r['ema_id'],r['nome'],r['principio_attivo'],r['stato_ema'],r['data_decisione_ue'],
                 r['indicazione_ema'],r['url_ema'],'old'))
        self.db.execute('INSERT INTO link_verificati VALUES (?,?,?)',
            (rec(0)['ema_id'],'049883135','EU/1/21/1608/013'))
    def tearDown(self):self.db.close()
    def test_expand_preserves_links(self):
        vals=[rec(i) for i in range(400)]
        first=importa_catalogo_ema(self.db,vals,'new',300)
        self.assertEqual(first['medicinali_archiviati'],300)
        self.assertEqual(first['nuovi_medicinali'],270)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM link_verificati').fetchone()[0],1)
        second=importa_catalogo_ema(self.db,vals,'new',300)
        self.assertEqual(second['nuovi_medicinali'],0)
        self.assertEqual(second['versioni_ema_osservate'],300)
    def test_history(self):
        vals=[rec(i) for i in range(100)]
        importa_catalogo_ema(self.db,vals,'new',30)
        vals[0]['stato_ema']='Withdrawn'
        importa_catalogo_ema(self.db,vals,'new',30)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ema_revisioni_snapshot WHERE ema_id=?',
            (rec(0)['ema_id'],)).fetchone()[0],2)
    def test_missing_id_rejected(self):
        with self.assertRaises(ValueError):
            importa_catalogo_ema(self.db,[rec(i) for i in range(1,101)],'new')
    def test_renamed_id_rejected(self):
        vals=[rec(i) for i in range(100)]
        vals[1]['nome']='Different'
        with self.assertRaises(ValueError):
            importa_catalogo_ema(self.db,vals,'new')

if __name__=='__main__':unittest.main()
