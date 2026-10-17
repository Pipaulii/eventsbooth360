"""Quote pricing, isolation, durable mail and private PDF access."""
import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import payments,app,server,quotes,gmail_mail,travel

class QuoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=server.DATABASE;server.DATABASE=Path(self.tmp.name)/'test.sqlite3'
        self.config=patch.object(payments,'config',return_value={'SITE_URL':'http://localhost','ADMIN_EMAIL':'owner@example.com','ADMIN_PASSWORD':'test-password-long'});self.config.start()
        self.client=app.app.test_client();self.headers={'Origin':'http://localhost'}
        r=self.client.post('/api/admin/login',json={'email':'owner@example.com','password':'test-password-long'},headers=self.headers)
        self.headers['X-CSRF-Token']=r.json['csrf']
        self.data=dict(request_key='test-request-123456789',name='Camille Exemple',email='client@example.com',phone='0600000000',address='10 rue Exemple, 13500 Martigues',event='Anniversaire',notes='Installation et animation incluses.',date='2099-11-24',time='18:00',hours='6',discount='10',travel_token='mock')
        self.trip=patch.object(travel,'verify',return_value=dict(fee_cents=1062,round_trip_km=81.7,label='10 rue Exemple, 13500 Martigues'));self.trip.start()
    def tearDown(self):self.trip.stop();self.config.stop();server.DATABASE=self.old;self.tmp.cleanup()
    def test_pricing(self):
        for h,total in [(2,22900),(3,29900),(4,35900),(6,53850),(8,71800)]:self.assertEqual(quotes.amounts(h,0,0)['total'],total)
        a=quotes.amounts(6,10,1062);self.assertEqual(a['total'],49527);self.assertEqual(a['deposit']+a['balance'],a['total'])
        for h,d in [('NaN',0),(2.5,0),(25,0),(6,-10),(6,'Infinity')]:
            with self.assertRaises(ValueError):quotes.amounts(h,d,0)
    def test_create_duplicate_and_no_calendar(self):
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send',return_value='message-id') as send:
            r=self.client.post('/api/admin/quotes',json=self.data,headers=self.headers);self.assertEqual(r.status_code,200)
            again=self.client.post('/api/admin/quotes',json=self.data,headers=self.headers);self.assertEqual(r.json,again.json)
            self.assertEqual(send.call_count,2);self.assertEqual({c.args[0] for c in send.call_args_list},{gmail_mail.OWNER,'client@example.com'})
            self.assertTrue(send.call_args.args[4][1].startswith(b'%PDF'))
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
        pdf=self.client.get('/api/admin/quotes/'+r.json['id']+'/pdf');self.assertEqual(pdf.status_code,200);self.assertEqual(pdf.headers['Cache-Control'],'no-store')
        public=app.app.test_client();self.assertEqual(public.get('/api/admin/quotes/'+r.json['id']+'/pdf').status_code,401)
        self.assertEqual(self.client.post('/api/admin/quotes',json=self.data).status_code,403)
    def test_pending_and_ambiguous_send_never_retried(self):
        with patch.object(gmail_mail,'access_token',side_effect=RuntimeError()):r=self.client.post('/api/admin/quotes',json=self.data,headers=self.headers)
        qid=r.json['id']
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send',side_effect=TimeoutError()) as send:
            quotes.deliver(qid);quotes.deliver(qid);self.assertEqual(send.call_count,2)
        mails=self.client.get('/api/admin/quotes').json['quotes'][0]['emails'];self.assertTrue(all(m['status']=='unknown' for m in mails))
    def test_invalid_trip_no_creation(self):
        with patch.object(travel,'verify',side_effect=travel.TravelError('Recalculez')):
            self.assertEqual(self.client.post('/api/admin/quotes',json=self.data,headers=self.headers).status_code,400)
        self.assertEqual(self.client.get('/api/admin/quotes').json['quotes'],[])

if __name__=='__main__':unittest.main()
