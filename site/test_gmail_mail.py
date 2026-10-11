"""Isolated Gmail tests: no real mail, customer cancellation or Google access."""
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parent))
import payments
import app,server,admin,gmail_mail

class GmailTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=server.DATABASE
        server.DATABASE=Path(self.temp.name)/'isolated.sqlite3'
        self.cfg=patch.object(payments,'config',return_value={'SITE_URL':'http://localhost','ADMIN_EMAIL':'owner@example.com','ADMIN_PASSWORD':'long-test-password-123','GMAIL_CLIENT_ID':'client','GMAIL_CLIENT_SECRET':'private-test-secret'})
        self.cfg.start();self.browser=app.app.test_client()
        self.headers={'Origin':'http://localhost'}
    def tearDown(self):
        self.cfg.stop();server.DATABASE=self.old;self.temp.cleanup()
    def login(self):
        r=self.browser.post('/api/admin/login',json={'email':'owner@example.com','password':'long-test-password-123'},headers=self.headers)
        self.headers['X-CSRF-Token']=r.json['csrf']
    def authorize(self):
        self.login()
        r=self.browser.post('/api/admin/gmail/connect',json={},headers=self.headers)
        params=parse_qs(urlparse(r.json['url']).query)
        self.assertEqual(params['scope'],[gmail_mail.SCOPE]);self.assertEqual(params['code_challenge_method'],['S256'])
        self.assertIn('HttpOnly',r.headers['Set-Cookie']);self.assertIn('SameSite=Lax',r.headers['Set-Cookie'])
        return params['state'][0]
    def seed(self):
        with server.connect() as db:
            payments.schema(db);admin.schema(db)
            db.execute("INSERT INTO bookings VALUES('b',1,22900,6870,'cancelled_admin','cs','10 rue Exemple, Marseille',1)")
            db.execute("INSERT INTO booking_contacts VALUES('b','Jean','Exemple','0612345678')")
            db.execute("INSERT INTO booking_personalization VALUES('b','Mariage','Jean et Jeanne')")
            db.execute("INSERT INTO admin_cancellations(booking_id,start,end,refund_status,refunded,created) VALUES('b','2026-12-12T18:30:00','2026-12-12T20:30:00','pending',6870,1)")
            gmail_mail.queue_cancellation(db,'b','client@example.com')
    def test_auth_and_csrf(self):
        self.assertEqual(self.browser.get('/api/admin/gmail/status').status_code,401)
        self.login()
        self.assertEqual(self.browser.post('/api/admin/gmail/connect',json={}).status_code,403)
    def test_callback_state_replay_and_encryption(self):
        state=self.authorize()
        with patch.object(gmail_mail,'token_request',return_value={'scope':gmail_mail.SCOPE,'refresh_token':'sensitive-refresh-token'}) as exchange:
            r=self.browser.get('/api/admin/gmail/callback',query_string={'state':state,'code':'one-time-code'})
            self.assertEqual(r.status_code,302);self.assertTrue(gmail_mail.connected())
            self.assertEqual(self.browser.get('/api/admin/gmail/callback',query_string={'state':state,'code':'replay'}).status_code,400)
            self.assertEqual(exchange.call_count,1)
        with server.connect() as db:
            stored=db.execute('SELECT value FROM gmail_connection').fetchone()[0]
        self.assertNotIn('sensitive-refresh-token',stored)
    def test_expired_or_wrong_state_rejected(self):
        state=self.authorize()
        with patch.object(gmail_mail,'token_request') as exchange:
            self.assertEqual(self.browser.get('/api/admin/gmail/callback?state=wrong&code=x').status_code,400)
            with server.connect() as db:db.execute('UPDATE gmail_states SET expires=0')
            r=self.browser.get('/api/admin/gmail/callback',query_string={'state':state,'code':'x'})
            self.assertIn('gmail=error',r.location);exchange.assert_not_called()
    def test_incomplete_grant_not_saved(self):
        state=self.authorize()
        with patch.object(gmail_mail,'token_request',return_value={'scope':'wrong','refresh_token':'test'}):
            r=self.browser.get('/api/admin/gmail/callback',query_string={'state':state,'code':'x'})
        self.assertIn('gmail=error',r.location);self.assertFalse(gmail_mail.connected())
    def test_details_two_recipients_and_idempotency(self):
        self.seed()
        with server.connect() as db:
            gmail_mail.queue_cancellation(db,'b','client@example.com')
            rows=db.execute('SELECT recipient,body FROM cancellation_mail').fetchall()
        self.assertEqual(len(rows),2)
        for recipient,body in rows:
            self.assertIn('Jean Exemple',body);self.assertIn('0612345678',body)
            self.assertIn('18:30',body);self.assertIn('Marseille',body)
            self.assertIn('encore en cours',body);self.assertNotIn('Remboursement effectué',body)
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send',return_value='gmail-id') as send:
            gmail_mail.deliver('b');gmail_mail.deliver('b');self.assertEqual(send.call_count,2)
    def test_ambiguous_send_never_retried(self):
        self.seed()
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send',side_effect=TimeoutError()) as send:
            gmail_mail.deliver('b');gmail_mail.deliver('b');self.assertEqual(send.call_count,2)
        with server.connect() as db:self.assertEqual({r[0] for r in db.execute('SELECT status FROM cancellation_mail').fetchall()},{'unknown'})
    def test_no_connection_keeps_messages_pending(self):
        self.seed();gmail_mail.deliver('b')
        with server.connect() as db:self.assertEqual({r[0] for r in db.execute('SELECT status FROM cancellation_mail').fetchall()},{'pending'})
    def test_test_mail_owner_only(self):
        self.login()
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send') as send:
            r=self.browser.post('/api/admin/gmail/test',json={'recipient':'evil@example.com'},headers=self.headers)
            self.assertEqual(r.status_code,200);self.assertEqual(send.call_args.args[0],gmail_mail.OWNER)

    def test_signature_embedded_and_customer_text_escaped(self):
        message=gmail_mail.compose('client@example.com','Test','Client : <script>test</script>\nLieu : A & B')
        html=message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('cid:eventsbooth360-signature',html)
        self.assertIn('tel:+33611574664',html)
        self.assertIn('https://eventsbooth360.fr',html)
        images=[part for part in message.walk() if part.get_content_type()=='image/png']
        self.assertEqual(len(images),1)
        self.assertEqual(images[0]['Content-ID'],'<eventsbooth360-signature>')
        self.assertTrue(images[0].get_payload(decode=True).startswith(b'\x89PNG'))
        self.assertIn('06 11 57 46 64',message.get_body(preferencelist=('plain',)).get_content())

    def test_new_reservation_details_and_single_delivery(self):
        self.seed()
        with server.connect() as db:
            db.execute("INSERT INTO slots(id,start,end) VALUES(1,'2026-11-26T18:00:00','2026-11-26T20:00:00')")
            db.execute("UPDATE bookings SET status='confirmed' WHERE id='b'")
            db.execute("UPDATE booking_contacts SET first_name='Camille',last_name='Exemple',phone='06 00 00 00 00' WHERE booking_id='b'")
            db.execute("UPDATE booking_personalization SET event_type='Anniversaire',video_name='Les 30 ans de Camille' WHERE booking_id='b'")
            gmail_mail.queue_reservation(db,'b');gmail_mail.queue_reservation(db,'b')
            rows=db.execute('SELECT recipient,body FROM reservation_mail').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],gmail_mail.OWNER)
        for detail in ['Camille','Exemple','06 00 00 00 00','10 rue Exemple','Anniversaire','Les 30 ans de Camille','26/11/2026','18:00','20:00','229,00','68,70','160,30']:
            self.assertIn(detail,rows[0][1])
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send',return_value='message') as send:
            gmail_mail.deliver_reservation('b');gmail_mail.deliver_reservation('b')
            self.assertEqual(send.call_count,1)

    def test_customer_pdf_numbering_attachment_and_idempotency(self):
        self.seed()
        with server.connect() as db:
            db.execute("INSERT INTO slots(id,start,end) VALUES(1,'2026-11-26T18:00:00','2026-11-26T20:00:00')")
            gmail_mail.queue_reservation(db,'b','client@example.com')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reservation_documents').fetchone()[0],0)
            db.execute("UPDATE bookings SET status='confirmed' WHERE id='b'")
            gmail_mail.queue_reservation(db,'b','client@example.com')
            gmail_mail.queue_reservation(db,'b','client@example.com')
            docs=db.execute('SELECT number,pdf FROM reservation_documents').fetchall()
            self.assertEqual(len(docs),1);self.assertEqual(docs[0][0],11)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reservation_mail').fetchone()[0],2)
            db.execute("INSERT INTO bookings VALUES('next',1,22900,6870,'confirmed','cs2','Marseille',1)")
            gmail_mail.queue_reservation(db,'next','next@example.com')
            self.assertEqual(db.execute("SELECT number FROM reservation_documents WHERE booking_id='next'").fetchone()[0],12)
        with patch.object(gmail_mail,'access_token',return_value='mock'),patch.object(gmail_mail,'send',return_value='message') as send:
            gmail_mail.deliver_reservation('b');gmail_mail.deliver_reservation('b')
            self.assertEqual(send.call_count,2)
            for call in send.call_args_list:
                attachment=call.kwargs['attachment']
                self.assertTrue(attachment[1].startswith(b'%PDF'))
                message=gmail_mail.compose(*call.args[:3],attachment=attachment)
                parts=list(message.iter_attachments())
                self.assertEqual(len(parts),1)
                self.assertEqual(parts[0].get_content_type(),'application/pdf')
                self.assertEqual(parts[0].get_payload(decode=True),attachment[1])

if __name__=='__main__':unittest.main()
