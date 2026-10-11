import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import payments
import app,server,visits
class VisitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=server.DATABASE;server.DATABASE=Path(self.tmp.name)/'test.sqlite3'
        self.config=patch.object(payments,'config',return_value={'SITE_URL':'http://localhost','ADMIN_EMAIL':'owner@example.com','ADMIN_PASSWORD':'long-test-password-123'});self.config.start();self.client=app.app.test_client()
    def tearDown(self):self.config.stop();server.DATABASE=self.old;self.tmp.cleanup()
    def test_public_views_only_and_private_metrics(self):
        self.client.get('/');self.client.get('/?utm_source=test');self.client.get('/location-videobooth-360-marseille')
        for url in ['/admin','/admin.html','/api/payment-status','/robots.txt','/sitemap.xml','/style.css','/missing-page']:self.client.get(url)
        self.client.head('/')
        self.assertEqual(visits.summary()['total'],3)
        self.assertEqual(self.client.get('/api/admin/visits').status_code,401)
        result=self.client.post('/api/admin/login',json={'email':'owner@example.com','password':'long-test-password-123'},headers={'Origin':'http://localhost'})
        self.assertEqual(result.status_code,200)
        result=self.client.get('/api/admin/visits');self.assertEqual(result.json['today'],3);self.assertEqual(result.headers['Cache-Control'],'no-store');self.assertIn('noindex',result.headers['X-Robots-Tag'])
        with server.connect() as db:self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(site_page_views)')],['day','views'])
    def test_counter_failure_does_not_break_page(self):
        with patch.object(visits,'record',side_effect=RuntimeError('offline')):self.assertEqual(self.client.get('/').status_code,200)
if __name__=='__main__':unittest.main()

