"""Administration tests: only mocked Stripe/iCloud and an isolated database."""
import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import payments
import app,server,admin,apple_calendar

class AdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=server.DATABASE;server.DATABASE=Path(self.tmp.name)/'admin.sqlite3'
        self.config=patch.object(payments,'config',return_value={'SITE_URL':'http://localhost','ADMIN_EMAIL':'owner@example.com','ADMIN_PASSWORD':'long-test-password-123'});self.config.start()
        self.browser=app.app.test_client();self.headers={'Origin':'http://localhost'}
        self.refunds=[]
        def refund(params,options):
            self.refunds.append((params,options));return NS(id='re_mock',status='succeeded',amount=6870)
        self.stripe=NS(v1=NS(checkout=NS(sessions=NS(retrieve=lambda sid:NS(payment_status='paid',amount_total=6870,currency='eur',metadata={'booking_id':'booking'},payment_intent='pi_mock'))),payment_intents=NS(retrieve=lambda *args:NS(latest_charge=NS(amount_refunded=0,amount=6870))),refunds=NS(create=refund,retrieve=lambda rid:NS(status='succeeded'))))
    def tearDown(self):self.config.stop();server.DATABASE=self.old;self.tmp.cleanup()
    def login(self):
        result=self.browser.post('/api/admin/login',json={'email':'owner@example.com','password':'long-test-password-123'},headers=self.headers)
        self.assertEqual(result.status_code,200);self.headers['X-CSRF-Token']=result.json['csrf']
    def booking(self,status='confirmed'):
        with server.connect() as db:
            payments.schema(db);admin.schema(db)
            slot=db.execute("INSERT INTO slots(start,end) VALUES('2026-12-12T19:00:00','2026-12-12T21:00:00')").lastrowid
            db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',('booking',slot,22900,6870,status,'cs_mock','Adresse exemple',1))
    def test_auth_csrf_and_hashed_password(self):
        self.assertEqual(self.browser.get('/api/admin/bookings').status_code,401)
        self.assertEqual(self.browser.post('/api/admin/login',json={},headers={'Origin':'https://evil.example'}).status_code,403)
        self.login()
        with server.connect() as db:
            hashed=db.execute('SELECT password_hash FROM admin_users').fetchone()[0]
            self.assertNotIn('long-test-password',hashed)
        self.assertEqual(self.browser.post('/api/admin/cancel',json={'booking_id':'booking','confirm':True},headers={'Origin':'http://localhost'}).status_code,403)
        self.assertIn('noindex',self.browser.get('/admin').headers['X-Robots-Tag'])
        self.assertEqual(self.browser.get('/api/admin/bookings').headers['Cache-Control'],'no-store')
    def test_refund_is_idempotent_and_calendar_retry(self):
        self.booking();self.login()
        with patch.object(payments,'client',return_value=self.stripe),patch.object(apple_calendar,'remove_booking',side_effect=apple_calendar.CalendarUnavailable('offline')):
            for _ in range(2):self.assertEqual(self.browser.post('/api/admin/cancel',json={'booking_id':'booking','confirm':True},headers=self.headers).status_code,200)
        self.assertEqual(len(self.refunds),1);self.assertEqual(self.refunds[0][1]['idempotency_key'],'admin-cancel-booking')
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM bookings').fetchone()[0],'cancelled_admin')
            self.assertEqual(db.execute('SELECT calendar_done FROM admin_cancellations').fetchone()[0],0)
        with patch.object(apple_calendar,'remove_booking') as remove:admin.sync_calendar();remove.assert_called_once_with('booking')
    def test_stripe_failure_preserves_slot_and_late_webhook_cannot_revive(self):
        self.booking();self.login()
        with patch.object(payments,'client',side_effect=RuntimeError('offline')):
            self.assertEqual(self.browser.post('/api/admin/cancel',json={'booking_id':'booking','confirm':True},headers=self.headers).status_code,503)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM bookings').fetchone()[0],'cancel_pending')
        event={'id':'evt_late','livemode':False,'type':'checkout.session.completed','data':{'object':{'id':'cs_mock','metadata':{'booking_id':'booking'},'payment_status':'paid','amount_total':6870,'currency':'eur'}}}
        with patch.object(payments,'config',return_value={'STRIPE_WEBHOOK_SECRET':'whsec_mock'}),patch.object(payments.stripe.Webhook,'construct_event',return_value=NS(to_dict=lambda:event)):
            payments.webhook(b'', '',server.connect)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM bookings').fetchone()[0],'cancel_pending')
    def test_already_refunded_and_calendar_cancelled(self):
        self.booking('cancelled_calendar');self.login()
        with server.connect() as db:
            db.execute("INSERT INTO calendar_cancellations VALUES('booking','2026-12-12T19:00:00','2026-12-12T21:00:00',1)");db.execute('DELETE FROM slots')
        self.stripe.v1.payment_intents.retrieve=lambda *args:NS(latest_charge=NS(amount_refunded=6870,amount=6870))
        with patch.object(payments,'client',return_value=self.stripe),patch.object(apple_calendar,'remove_booking'):
            self.assertEqual(self.browser.post('/api/admin/cancel',json={'booking_id':'booking','confirm':True},headers=self.headers).status_code,200)
        self.assertEqual(self.refunds,[])
    def test_pending_refund_is_not_marked_succeeded(self):
        self.booking();self.login()
        self.stripe.v1.refunds.create=lambda *args,**kwargs:NS(id='re_mock',status='pending',amount=6870)
        with patch.object(payments,'client',return_value=self.stripe),patch.object(apple_calendar,'remove_booking'):
            self.assertEqual(self.browser.post('/api/admin/cancel',json={'booking_id':'booking','confirm':True},headers=self.headers).status_code,200)
        with server.connect() as db:self.assertEqual(db.execute('SELECT refund_status FROM admin_cancellations').fetchone()[0],'pending')

    def test_change_account_requires_current_password_and_invalidates_sessions(self):
        self.login()
        old_cookie=self.browser.get_cookie(admin.COOKIE).value
        data={'current_password':'wrong','new_password':'New-1234','confirmation':'New-1234'}
        self.assertEqual(self.browser.post('/api/admin/account',json=data,headers=self.headers).status_code,400)
        data['current_password']='long-test-password-123'
        self.assertEqual(self.browser.post('/api/admin/account',json=data,headers={'Origin':'http://localhost'}).status_code,403)
        data['confirmation']='different'
        self.assertEqual(self.browser.post('/api/admin/account',json=data,headers=self.headers).status_code,400)
        short=dict(data,new_password='1234567',confirmation='1234567')
        self.assertEqual(self.browser.post('/api/admin/account',json=short,headers=self.headers).status_code,400)
        data['confirmation']=data['new_password']
        self.assertEqual(self.browser.post('/api/admin/account',json=data,headers=self.headers).status_code,200)
        self.browser.set_cookie(admin.COOKIE,old_cookie)
        self.assertEqual(self.browser.get('/api/admin/bookings').status_code,401)
        with server.connect() as db:
            email,hashed=db.execute('SELECT email,password_hash FROM admin_users').fetchone()
        self.assertEqual(email,'eventsbooth360@gmail.com');self.assertNotIn(data['new_password'],hashed)
        self.assertEqual(self.browser.post('/api/admin/login',json={'email':email,'password':data['new_password']},headers={'Origin':'http://localhost'}).status_code,200)

    def test_account_change_rate_limit(self):
        self.login()
        data={'current_password':'wrong','new_password':'New-1234','confirmation':'New-1234'}
        for _ in range(5):self.assertEqual(self.browser.post('/api/admin/account',json=data,headers=self.headers).status_code,400)
        self.assertEqual(self.browser.post('/api/admin/account',json=data,headers=self.headers).status_code,429)

    def test_fixture_cleanup_preserves_real_bookings(self):
        self.booking()
        with server.connect() as db:
            slot=db.execute("INSERT INTO slots(start,end) VALUES('2026-11-26T18:00:00','2026-11-26T20:00:00')").lastrowid
            db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',('TEST-CAMILLE-20261126',slot,0,0,'confirmed',None,'TEST',1))
        with patch.object(apple_calendar,'remove_booking'):
            admin.cleanup_requested_tests();admin.cleanup_requested_tests()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM bookings WHERE id='TEST-CAMILLE-20261126'").fetchone()[0],'removed_test')
            self.assertEqual(db.execute("SELECT status FROM bookings WHERE id='booking'").fetchone()[0],'confirmed')
            self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0],1)


    def test_manual_reservation_without_stripe(self):
        self.login()
        data=dict(first_name='Camille',last_name='Exemple',phone='0600000000',address='10 rue Exemple Marseille',date='2026-12-20',time='18:00',duration=2,event_type='Autre',video_name='Soiree')
        with patch.object(apple_calendar,'busy',return_value=[]),patch.object(server,'openings',return_value=['18:00']),patch.object(apple_calendar,'export_pending'),patch('gmail_mail.deliver_reservation'),patch.object(payments,'client',side_effect=AssertionError('Stripe must not be called')):
            result=self.browser.post('/api/admin/reservations',json=data,headers=self.headers)
        self.assertEqual(result.status_code,200)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT total,due,status FROM bookings').fetchone(),(0,0,'confirmed'))
            self.assertEqual(db.execute('SELECT first_name,last_name,phone FROM booking_contacts').fetchone(),('Camille','Exemple','0600000000'))

if __name__=='__main__':unittest.main()
