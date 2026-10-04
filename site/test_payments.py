import sys, tempfile, json, time, hmac, hashlib
from pathlib import Path
from datetime import datetime,timedelta
from types import SimpleNamespace
sys.path.insert(0,str(Path('site').resolve()))
import server,payments
with tempfile.TemporaryDirectory() as folder:
 server.DATABASE=Path(folder)/'test.sqlite3'
 payments.config=lambda:{'STRIPE_SECRET_KEY':'sk_test_placeholder','STRIPE_WEBHOOK_SECRET':'whsec_test','SITE_URL':'http://127.0.0.1:3600'}
 calls=[]
 def create(params,options):
  calls.append(params);return SimpleNamespace(id='cs_test_mock',url='https://checkout.stripe.com/c/test')
 payments.client=lambda:SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create))))
 with server.connect() as db:db.execute("INSERT INTO settings VALUES('ready','true')")
 day=(datetime.now()+timedelta(days=3)).strftime('%Y-%m-%d');data={'duration':2,'payment_mode':'deposit','date':day,'time':'17:00','address':'10 rue Exemple, 13001 Marseille','first_name':'Marie','last_name':'Exemple','phone':'06 12 34 56 78','event_type':'Mariage','video_name':'Marie et Alex'}
 result=payments.checkout(data,server.connect,server.openings,server.setting)
 assert calls[0]['line_items'][0]['price_data']['unit_amount']==6870
 try:payments.checkout(data,server.connect,server.openings,server.setting);raise AssertionError('Double reservation acceptee')
 except payments.PaymentError:pass
 bid=calls[0]['metadata']['booking_id']
 event={'id':'evt_mock','object':'event','livemode':False,'type':'checkout.session.completed','data':{'object':{'id':'cs_test_mock','object':'checkout.session','metadata':{'booking_id':bid},'payment_status':'paid','amount_total':6870,'currency':'eur'}}}
 raw=json.dumps(event).encode();stamp=str(int(time.time()));sig=hmac.new(b'whsec_test',stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest();header=f't={stamp},v1={sig}'
 payments.webhook(raw,header,server.connect);payments.webhook(raw,header,server.connect)
 with server.connect() as db:
  assert db.execute('SELECT status FROM bookings').fetchone()[0]=='confirmed'
  assert db.execute('SELECT first_name,last_name,phone FROM booking_contacts').fetchone()==('Marie','Exemple','06 12 34 56 78')
  assert db.execute('SELECT count(*) FROM stripe_events').fetchone()[0]==1
  assert db.execute('SELECT event_type,video_name FROM booking_personalization').fetchone()==('Mariage','Marie et Alex')
 try:payments.webhook(raw,'t=1,v1=invalid',server.connect);raise AssertionError('Signature invalide acceptee')
 except payments.PaymentError:pass
 print('OK : acompte, blocage concurrent, webhook signe, idempotence et rejet signature invalide.')
