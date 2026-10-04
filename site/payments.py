"""Stripe Checkout avec environnements distincts. No secrets served to the browser."""
from pathlib import Path
import os,sys,json,secrets,time,re
from datetime import datetime,timedelta
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.vendor'))
import stripe
class PaymentError(Exception):pass
def config():
    values={}
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"').strip("'")
    return {**values,**os.environ}
def live_mode():
    return config().get('STRIPE_MODE','test')=='live'
def client():
    key=config().get('STRIPE_SECRET_KEY','')
    prefixes=('sk_live_','rk_live_') if live_mode() else ('sk_test_','rk_test_')
    if not key.startswith(prefixes):raise PaymentError('La clé Stripe ne correspond pas à cet environnement.')
    return stripe.StripeClient(key,stripe_version='2026-08-26.dahlia',max_network_retries=2,http_client=stripe.UrllibClient())
def ready():
    from urllib.parse import urlparse
    c=config();prefixes=('sk_live_','rk_live_') if live_mode() else ('sk_test_','rk_test_')
    valid=c.get('STRIPE_SECRET_KEY','').startswith(prefixes) and c.get('STRIPE_WEBHOOK_SECRET','').startswith('whsec_')
    if live_mode():
        url=urlparse(c.get('SITE_URL',''))
        valid=valid and url.scheme=='https' and bool(url.hostname) and url.hostname not in ('localhost','127.0.0.1','::1')
    return bool(valid)
def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY,slot_id INTEGER NOT NULL,total INTEGER NOT NULL,due INTEGER NOT NULL,status TEXT NOT NULL,session_id TEXT UNIQUE,event_address TEXT NOT NULL,created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS stripe_events(id TEXT PRIMARY KEY,created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS booking_contacts(booking_id TEXT PRIMARY KEY,first_name TEXT NOT NULL,last_name TEXT NOT NULL,phone TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS booking_personalization(booking_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,video_name TEXT NOT NULL)')
    db.commit()
def checkout(data,connect,openings,setting):
    if not ready():raise PaymentError('Paiement non configuré pour cet environnement.')
    try:
        duration=int(data['duration']);mode=data['payment_mode'];start=datetime.fromisoformat(data['date']+'T'+data['time']);address=str(data['address']).strip()
        first=data['first_name'].strip();last=data['last_name'].strip();phone=data['phone'].strip()
        event_type=data['event_type'];video_name=data.get('video_name','').strip()
        if event_type not in ('Mariage','Baptême','Anniversaire','Soirée privée','Événement professionnel','Autre') or len(video_name)>120 or any(ord(c)<32 for c in video_name):raise ValueError()
        if not all(1<=len(n)<=80 and not any(ord(c)<32 for c in n) for n in (first,last)):raise ValueError()
        if len(phone)>30 or not re.fullmatch(r'\+?[\d\s().-]+',phone) or not 9<=len(re.sub(r'\D','',phone))<=15:raise ValueError()
        if duration not in (2,3,4) or mode not in ('deposit','full') or start.tzinfo or start.date()<=datetime.now().date() or len(address)<10 or len(address)>500:raise ValueError()
        if start.minute not in (0,30) or start.second:raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError):raise PaymentError('Vérifiez vos nom, prénom, téléphone, adresse et créneau. Réservez au plus tôt demain.')
    pricing=json.loads((ROOT/'public/pricing.json').read_text());total=pricing['packages'][str(duration)]['price_cents'];due=total if mode=='full' else (total*pricing['deposit_percent']+50)//100
    booking_id=secrets.token_hex(16);end=start+timedelta(hours=duration)
    import apple_calendar
    try:external=apple_calendar.busy(start.date(),start.date()+timedelta(days=1),fresh=True)
    except apple_calendar.CalendarUnavailable:raise PaymentError('Agenda Apple temporairement indisponible. Réessayez avant de payer.') from None
    original_openings=openings
    if apple_calendar.enabled():
        openings=lambda db,day,hours:original_openings(db,day,hours,extra=external)
    with connect() as db:
        schema(db);db.execute('BEGIN IMMEDIATE')
        if setting(db,'ready','false')!='true':raise PaymentError('Le planning réel doit être renseigné avant toute réservation.')
        if start.strftime('%H:%M') not in openings(db,start.date(),duration):raise PaymentError('Ce créneau vient d’être réservé. Choisissez un autre horaire.')
        slot=db.execute('INSERT INTO slots(start,end) VALUES(?,?)',(start.isoformat(),end.isoformat())).lastrowid
        db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',(booking_id,slot,total,due,'pending',None,address,int(time.time())))
        db.execute('INSERT INTO booking_contacts VALUES(?,?,?,?)',(booking_id,first,last,phone))
        db.execute('INSERT INTO booking_personalization VALUES(?,?,?)',(booking_id,event_type,video_name))
    origin=config().get('SITE_URL','http://127.0.0.1:3600').rstrip('/')
    label='Acompte 30 %' if mode=='deposit' else 'Paiement total'
    params={'mode':'payment','locale':'fr','customer_creation':'always','billing_address_collection':'required','phone_number_collection':{'enabled':True},'invoice_creation':{'enabled':True},'integration_identifier':'eventsbooth360_'+''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(8)),'client_reference_id':booking_id,'metadata':{'booking_id':booking_id},'line_items':[{'quantity':1,'price_data':{'currency':'eur','unit_amount':due,'product_data':{'name':f'EventsBooth360 — {duration} h — {label}','description':f'{start:%d/%m/%Y %H:%M} · Total formule {total/100:.2f} EUR · Solde {(total-due)/100:.2f} EUR'}}}],'expires_at':int(time.time())+1800,'success_url':origin+'/?payment=processing#disponibilites','cancel_url':origin+'/?payment=cancelled#disponibilites'}
    try:session=client().v1.checkout.sessions.create(params,options={'idempotency_key':booking_id})
    except Exception:
        # Keep the hold on ambiguous API failures: a remote session may have been created.
        with connect() as db:db.execute('UPDATE bookings SET status=? WHERE id=?',('review',booking_id))
        raise PaymentError('Stripe est indisponible. Ce créneau reste en attente de vérification ; ne recommencez pas le paiement.')
    with connect() as db:db.execute('UPDATE bookings SET session_id=? WHERE id=?',(session.id,booking_id))
    return {'url':session.url}
def webhook(payload,signature,connect):
    secret=config().get('STRIPE_WEBHOOK_SECRET','')
    if not secret.startswith('whsec_'):raise PaymentError('Webhook non configuré.')
    try:event=stripe.Webhook.construct_event(payload,signature,secret).to_dict()
    except Exception:raise PaymentError('Signature Stripe invalide.')
    if bool(event.get('livemode'))!=live_mode():raise PaymentError('Événement Stripe du mauvais environnement.')
    obj=event['data']['object'];kind=event['type'];bid=obj.get('metadata',{}).get('booking_id')
    with connect() as db:
        schema(db);db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM stripe_events WHERE id=?',(event['id'],)).fetchone():return
        booking=db.execute('SELECT slot_id,due,status,session_id FROM bookings WHERE id=?',(bid,)).fetchone()
        if booking and kind.startswith('checkout.session.'):
            slot,due,status,sid=booking
            if status in ('cancelled_calendar','cancel_pending','cancelled_admin'):
                db.execute('INSERT INTO stripe_events VALUES(?,?)',(event['id'],int(time.time())))
                return
            if sid and sid!=obj['id']:raise PaymentError('Session incohérente.')
            if kind in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
                if obj.get('payment_status')=='paid':
                    if obj.get('amount_total')!=due or obj.get('currency')!='eur':raise PaymentError('Montant incohérent.')
                    if status in ('expired','failed'):raise PaymentError('Réservation expirée : intervention nécessaire.')
                    db.execute('UPDATE bookings SET status=?,session_id=? WHERE id=?',('confirmed',obj['id'],bid))
                elif status!='confirmed':db.execute('UPDATE bookings SET status=? WHERE id=?',('processing',bid))
            elif kind in ('checkout.session.expired','checkout.session.async_payment_failed') and status!='confirmed':
                db.execute('DELETE FROM slots WHERE id=?',(slot,));db.execute('UPDATE bookings SET status=? WHERE id=?',('expired' if kind.endswith('expired') else 'failed',bid))
        db.execute('INSERT INTO stripe_events VALUES(?,?)',(event['id'],int(time.time())))
