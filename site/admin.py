"""Private administration: signed sessions and durable, idempotent refunds."""
import hashlib,hmac,secrets,time
from functools import wraps
from flask import Blueprint,request,jsonify,send_from_directory
from itsdangerous import URLSafeTimedSerializer,BadSignature,SignatureExpired
from werkzeug.security import generate_password_hash,check_password_hash
import payments,server,apple_calendar

bp=Blueprint('admin',__name__)
COOKIE='eventsbooth_admin'

def account():
    with server.connect() as db:
        schema(db);db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT email,password_hash FROM admin_users WHERE id=1').fetchone()
        if not row:
            c=payments.config();email=c.get('ADMIN_EMAIL','').strip().casefold();pw=c.get('ADMIN_PASSWORD','')
            if '@' not in email or len(pw)<8:return None
            db.execute('INSERT INTO admin_users VALUES(1,?,?)',(email,generate_password_hash(pw)))
            row=db.execute('SELECT email,password_hash FROM admin_users WHERE id=1').fetchone()
        return row
def signer():
    key=hashlib.sha256(('eventsbooth-admin-session:'+account()[1]).encode()).hexdigest()
    return URLSafeTimedSerializer(key,salt='admin-v1')
def configured():
    try:return bool(account())
    except Exception:return False
def session():
    if not configured():return None
    try:return signer().loads(request.cookies.get(COOKIE,''),max_age=1800)
    except (BadSignature,SignatureExpired):return None
def origin_ok():return request.headers.get('Origin')==payments.config().get('SITE_URL','').rstrip('/')
def protected(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        auth=session()
        if not auth:return jsonify(error='Connectez-vous à l’administration.'),401
        if request.method!='GET' and (not origin_ok() or not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),auth['csrf'])):
            return jsonify(error='Requête refusée.'),403
        return fn(*args,**kwargs)
    return wrapped
def schema(db):
    apple_calendar.schema(db)
    db.execute('CREATE TABLE IF NOT EXISTS admin_attempts(identity TEXT PRIMARY KEY, attempt_window INTEGER NOT NULL, attempts INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS admin_users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS admin_cancellations(booking_id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, refund_id TEXT, refund_status TEXT NOT NULL, refunded INTEGER NOT NULL DEFAULT 0, calendar_done INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL)')

@bp.get('/admin')
@bp.get('/admin/')
def page():return send_from_directory(server.ROOT/'public','admin.html')
@bp.get('/api/admin/session')
def state():
    auth=session()
    return jsonify(configured=configured(),authenticated=bool(auth),csrf=auth['csrf'] if auth else None)
@bp.post('/api/admin/login')
def login():
    if not configured():return jsonify(error='Ajoutez ADMIN_EMAIL et ADMIN_PASSWORD dans Render (mot de passe d’au moins 8 caractères).'),503
    if not origin_ok():return jsonify(error='Origine refusée.'),403
    data=request.get_json(silent=True) or {};candidate=data.get('password','')
    if not isinstance(candidate,str) or len(candidate)>512:return jsonify(error='Mot de passe invalide.'),400
    # Render supplies the connecting client's IP. No raw IP or password is stored.
    ip=request.headers.get('X-Forwarded-For',request.remote_addr or '').split(',')[0].strip()
    user=account();ident=hashlib.sha256((user[1]+ip).encode()).hexdigest();window=int(time.time())//600
    with server.connect() as db:
        schema(db);db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT attempt_window,attempts FROM admin_attempts WHERE identity=?',(ident,)).fetchone()
        if row and row[0]==window and row[1]>=5:return jsonify(error='Trop de tentatives. Réessayez dans 10 minutes.'),429
        global_row=db.execute("SELECT attempt_window,attempts FROM admin_attempts WHERE identity='global'").fetchone()
        if global_row and global_row[0]==window and global_row[1]>=30:return jsonify(error='Trop de tentatives. Réessayez dans 10 minutes.'),429
        db.execute('INSERT INTO admin_attempts VALUES(?,?,?) ON CONFLICT(identity) DO UPDATE SET attempt_window=EXCLUDED.attempt_window,attempts=EXCLUDED.attempts',('global',window,(global_row[1]+1 if global_row and global_row[0]==window else 1)))
        db.execute('DELETE FROM admin_attempts WHERE attempt_window < ?',(window-1,))
        db.execute('INSERT INTO admin_attempts VALUES(?,?,?) ON CONFLICT(identity) DO UPDATE SET attempt_window=EXCLUDED.attempt_window,attempts=EXCLUDED.attempts',(ident,window,(row[1]+1 if row and row[0]==window else 1)))
        if str(data.get('email','')).strip().casefold()!=user[0] or not check_password_hash(user[1],candidate):
            return jsonify(error='Mot de passe incorrect.'),401
        db.execute('DELETE FROM admin_attempts WHERE identity=?',(ident,))
    csrf=secrets.token_urlsafe(32);response=jsonify(authenticated=True,csrf=csrf)
    response.set_cookie(COOKIE,signer().dumps({'csrf':csrf}),max_age=1800,httponly=True,secure=payments.config().get('SITE_URL','').startswith('https://'),samesite='Strict',path='/')
    return response
@bp.post('/api/admin/logout')
@protected
def logout():
    response=jsonify(ok=True);response.delete_cookie(COOKIE,path='/');return response

@bp.post('/api/admin/account')
@protected
def change_account():
    data=request.get_json(silent=True) or {}
    current=data.get('current_password');password=data.get('new_password');confirmation=data.get('confirmation')
    if not isinstance(current,str) or len(current)>512 or not isinstance(password,str) or not 8<=len(password)<=512:
        return jsonify(error='Choisissez un mot de passe de 8 à 512 caractères.'),400
    if password!=confirmation:return jsonify(error='Les deux nouveaux mots de passe ne correspondent pas.'),400
    if password==current:return jsonify(error='Choisissez un mot de passe différent de l’ancien.'),400
    user=account();window=int(time.time())//600
    identity='account-change:'+hashlib.sha256(user[1].encode()).hexdigest()
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        stored=db.execute('SELECT password_hash FROM admin_users WHERE id=1').fetchone()
        if not stored or stored[0]!=user[1]:return jsonify(error='L’accès a déjà été modifié. Reconnectez-vous.'),409
        row=db.execute('SELECT attempt_window,attempts FROM admin_attempts WHERE identity=?',(identity,)).fetchone()
        attempts=row[1] if row and row[0]==window else 0
        if attempts>=5:return jsonify(error='Trop de tentatives. Réessayez dans 10 minutes.'),429
        if not check_password_hash(user[1],current):
            db.execute('INSERT INTO admin_attempts VALUES(?,?,?) ON CONFLICT(identity) DO UPDATE SET attempt_window=EXCLUDED.attempt_window,attempts=EXCLUDED.attempts',(identity,window,attempts+1))
            return jsonify(error='Le mot de passe actuel est incorrect.'),400
        # Compare the hash inside the update to avoid overwriting a concurrent change.
        db.execute('UPDATE admin_users SET email=?,password_hash=? WHERE id=1 AND password_hash=?',('eventsbooth360@gmail.com',generate_password_hash(password),user[1]))
        db.execute('DELETE FROM admin_attempts WHERE identity=?',(identity,))
    response=jsonify(ok=True,email='eventsbooth360@gmail.com')
    # The hash-derived signing key also invalidates all other admin sessions.
    response.delete_cookie(COOKIE,path='/')
    return response

def sync_calendar():
    """Retry failed calendar deletions; never recreate an admin-cancelled event."""
    with server.connect() as db:
        schema(db)
        rows=db.execute("SELECT booking_id FROM admin_cancellations WHERE calendar_done=0 AND refund_status IN ('succeeded','pending','no_payment','already_refunded') LIMIT 25").fetchall()
    for (bid,) in rows:
        try:apple_calendar.remove_booking(bid)
        except apple_calendar.CalendarUnavailable:continue
        with server.connect() as db:db.execute('UPDATE admin_cancellations SET calendar_done=1 WHERE booking_id=?',(bid,))

def cancel(bid):
    import gmail_mail
    customer_email=None
    with server.connect() as db:
        payments.schema(db);schema(db);db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT b.slot_id,b.due,b.status,b.session_id,s.start,s.end FROM bookings b LEFT JOIN slots s ON s.id=b.slot_id WHERE b.id=?',(bid,)).fetchone()
        if not row:raise payments.PaymentError('Réservation introuvable.')
        slot,due,status,sid,begin,finish=row
        existing=db.execute('SELECT refund_id,refund_status,refunded FROM admin_cancellations WHERE booking_id=?',(bid,)).fetchone()
        if existing and existing[1] in ('succeeded','pending','no_payment','already_refunded'):
            return
        if status not in ('confirmed','cancelled_calendar','cancel_pending','cancelled_admin'):
            raise payments.PaymentError('Seules les locations confirmées ou déjà annulées depuis Apple peuvent être remboursées ici.')
        if not begin:
            original=db.execute('SELECT start,end FROM calendar_cancellations WHERE booking_id=?',(bid,)).fetchone()
            if not original:raise payments.PaymentError('Horaires à vérifier avant annulation.')
            begin,finish=original
        db.execute("INSERT INTO admin_cancellations(booking_id,start,end,refund_status,created) VALUES(?,?,?,'review',?) ON CONFLICT(booking_id) DO NOTHING",(bid,begin,finish,int(time.time())))
        db.execute("UPDATE bookings SET status='cancel_pending' WHERE id=?",(bid,))
    # Durable intent before network I/O. Stable idempotency key prevents double refunds.
    if due==0:refund_id=None;refund_status='no_payment';amount=0
    else:
        if not sid:raise payments.PaymentError('Session Stripe manquante : vérifiez le paiement dans Stripe.')
        stripe=payments.client()
        checkout=stripe.v1.checkout.sessions.retrieve(sid)
        if checkout.payment_status!='paid' or checkout.amount_total!=due or checkout.currency!='eur' or checkout.metadata.get('booking_id')!=bid:
            raise payments.PaymentError('Paiement Stripe incohérent : intervention nécessaire.')
        details=getattr(checkout,'customer_details',None)
        customer_email=(details.get('email') if isinstance(details,dict) else getattr(details,'email',None))
        intent=stripe.v1.payment_intents.retrieve(checkout.payment_intent,{'expand':['latest_charge']})
        charge=intent.latest_charge
        if not charge or isinstance(charge,str):raise payments.PaymentError('Paiement non vérifiable.')
        if charge.amount_refunded==charge.amount:
            refund_id=None;refund_status='already_refunded';amount=charge.amount_refunded
        elif charge.amount_refunded:
            raise payments.PaymentError('Un remboursement partiel existe déjà. Terminez-le dans Stripe pour éviter un doublon.')
        else:
            refund=stripe.v1.refunds.create({'payment_intent':checkout.payment_intent,'metadata':{'booking_id':bid}},options={'idempotency_key':'admin-cancel-'+bid})
            refund_id=refund.id;refund_status=refund.status;amount=refund.amount
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE admin_cancellations SET refund_id=?,refund_status=?,refunded=? WHERE booking_id=?',(refund_id,refund_status,amount,bid))
        if refund_status in ('succeeded','pending','no_payment','already_refunded'):
            db.execute("UPDATE bookings SET status='cancelled_admin' WHERE id=?",(bid,));db.execute('DELETE FROM slots WHERE id=?',(slot,))
            gmail_mail.queue_cancellation(db,bid,customer_email)
    if refund_status not in ('succeeded','pending','no_payment','already_refunded'):
        raise payments.PaymentError('Remboursement à vérifier dans Stripe. Le créneau reste bloqué.')

def refresh_refunds():
    with server.connect() as db:
        schema(db);rows=db.execute("SELECT booking_id,refund_id FROM admin_cancellations WHERE refund_status='pending' AND refund_id IS NOT NULL LIMIT 5").fetchall()
    for bid,rid in rows:
        try:refund=payments.client().v1.refunds.retrieve(rid)
        except Exception:continue
        with server.connect() as db:db.execute('UPDATE admin_cancellations SET refund_status=? WHERE booking_id=?',(refund.status,bid))

@bp.get('/api/admin/bookings')
@protected
def bookings():
    import gmail_mail
    try:
        cleanup_requested_tests()
        refresh_refunds();sync_calendar()
        with server.connect() as db:
            payments.schema(db);schema(db)
            rows=db.execute("SELECT b.id,b.status,b.total,b.due,b.event_address,COALESCE(s.start,a.start,c.start),COALESCE(s.end,a.end,c.end),a.refund_status,a.refunded,a.calendar_done FROM bookings b LEFT JOIN slots s ON s.id=b.slot_id LEFT JOIN admin_cancellations a ON a.booking_id=b.id LEFT JOIN calendar_cancellations c ON c.booking_id=b.id WHERE b.status!='removed_test' ORDER BY b.created DESC LIMIT 200").fetchall()
            contacts={r[0]:r[1:] for r in db.execute('SELECT booking_id,first_name,last_name,phone FROM booking_contacts').fetchall()}
            themes={r[0]:r[1:] for r in db.execute('SELECT booking_id,event_type,video_name FROM booking_personalization').fetchall()}
            trips={r[0]:{'fee_cents':r[1],'round_trip_km':r[2]} for r in db.execute('SELECT booking_id,fee,round_trip_km FROM booking_travel').fetchall()}
            gmail_mail.schema(db)
            notifications={}
            for bid,recipient,status in db.execute('SELECT booking_id,recipient,status FROM cancellation_mail').fetchall():
                notifications.setdefault(bid,[]).append({'recipient':recipient,'status':status})
        keys=['id','status','total','due','address','start','end','refund_status','refunded','calendar_done']
        items=[]
        for row in rows:
            item=dict(zip(keys,row));item.update(dict(zip(['first_name','last_name','phone'],contacts.get(row[0],('','','')))));item.update(dict(zip(['event_type','video_name'],themes.get(row[0],('','')))));items.append(item)
        for item in items:
            item['emails']=notifications.get(item['id'],[]);item['travel']=trips.get(item['id'])
        return jsonify(bookings=items)
    except Exception:return jsonify(error='Réservations temporairement indisponibles.'),503
@bp.post('/api/admin/cancel')
@protected
def cancellation():
    data=request.get_json(silent=True) or {};bid=data.get('booking_id')
    if not isinstance(bid,str) or len(bid)>100 or data.get('confirm') is not True:return jsonify(error='Confirmez l’annulation et le remboursement.'),400
    try:
        cancel(bid);sync_calendar()
        import gmail_mail
        try:gmail_mail.deliver(bid)
        except Exception:pass
        return jsonify(ok=True)
    except payments.PaymentError as e:return jsonify(error=str(e)),400
    except Exception:return jsonify(error='Stripe indisponible ou autorisation manquante. Réessayez avec ce même bouton ; vérifiez Stripe si le problème persiste.'),503

@bp.get('/api/admin/visits')
@protected
def visit_counts():
    import visits
    try:return jsonify(visits.summary())
    except Exception:return jsonify(error='Compteur temporairement indisponible.'),503


def cleanup_requested_tests():
    # Explicit owner-requested cleanup, restricted to the fixtures created in this session.
    with server.connect() as db:
        payments.schema(db);schema(db)
        if server.setting(db,'calendar_cleanup_uid_v2','false')!='true':
            db.execute("UPDATE admin_cancellations SET calendar_done=0 WHERE booking_id IN (SELECT id FROM bookings WHERE status IN ('removed_test','cancelled_admin'))")
            db.execute("INSERT INTO settings(key,value) VALUES('calendar_cleanup_uid_v2','true') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value")
        rows=db.execute("SELECT b.id,b.slot_id,b.due,b.session_id,s.start,s.end,b.status FROM bookings b LEFT JOIN slots s ON s.id=b.slot_id WHERE b.id LIKE ? OR b.id LIKE ? OR b.id LIKE ? OR b.id=?",('TEST-20261009-%','TEST-AGENDA-%','TEST-CAMILLE-202611%','6d320eca69ee5133463a828668399e68')).fetchall()
    for bid,slot,due,sid,start,end,status in rows:
        if status=='removed_test':continue
        if due:
            if not sid:continue
            try:
                checkout=payments.client().v1.checkout.sessions.retrieve(sid)
                if checkout.payment_status=='paid':continue
                if checkout.status=='open':payments.client().v1.checkout.sessions.expire(sid)
            except Exception:continue
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if start and end:
                db.execute("INSERT INTO admin_cancellations(booking_id,start,end,refund_status,created) VALUES(?,?,?,'no_payment',?) ON CONFLICT(booking_id) DO NOTHING",(bid,start,end,int(time.time())))
            db.execute("UPDATE bookings SET status='removed_test' WHERE id=?",(bid,))
            db.execute('DELETE FROM slots WHERE id=?',(slot,))
    sync_calendar()

@bp.post('/api/admin/reservations')
@protected
def create_reservation():
    from datetime import datetime,timedelta
    import re,gmail_mail
    data=request.get_json(silent=True) or {}
    try:
        first=data['first_name'].strip();last=data['last_name'].strip();phone=data['phone'].strip();address=data['address'].strip()
        event=data['event_type'];video=data.get('video_name','').strip()
        duration=int(data['duration']);start=datetime.fromisoformat(data['date']+'T'+data['time'])
        if not all(1<=len(n)<=80 and not any(ord(c)<32 for c in n) for n in (first,last)):raise ValueError()
        if not re.fullmatch(r'\+?[\d ().-]{9,30}',phone) or not 9<=len(re.sub(r'\D','',phone))<=15:raise ValueError()
        if not 10<=len(address)<=500 or len(video)>120:raise ValueError()
        if event not in ('Mariage','Baptême','Anniversaire','Soirée privée','Événement professionnel','Autre'):raise ValueError()
        if duration not in (2,3,4) or start.tzinfo or start.date()<datetime.now().date() or start.minute not in (0,30) or start.second:raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError):return jsonify(error='Vérifiez les coordonnées, la date et les horaires.'),400
    email=data.get('email','').strip()
    if email and not gmail_mail.valid_email(email):return jsonify(error='Adresse e-mail invalide.'),400
    bid='MANUEL-'+secrets.token_hex(12);end=start+timedelta(hours=duration)
    try:
        external=apple_calendar.busy(start.date(),start.date()+timedelta(days=1),fresh=True)
        with server.connect() as db:
            payments.schema(db);db.execute('BEGIN IMMEDIATE')
            if start.strftime('%H:%M') not in server.openings(db,start.date(),duration,extra=external):return jsonify(error='Ce créneau est occupé ou hors des horaires disponibles.'),409
            slot=db.execute('INSERT INTO slots(start,end) VALUES(?,?)',(start.isoformat(),end.isoformat())).lastrowid
            db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',(bid,slot,0,0,'confirmed',None,address,int(time.time())))
            db.execute('INSERT INTO booking_contacts VALUES(?,?,?,?)',(bid,first,last,phone))
            db.execute('INSERT INTO booking_personalization VALUES(?,?,?)',(bid,event,video))
            gmail_mail.queue_reservation(db,bid,email)
        try:gmail_mail.deliver_reservation(bid)
        except Exception:pass
        try:apple_calendar.export_pending(server.connect)
        except Exception:pass
        return jsonify(ok=True,booking_id=bid)
    except apple_calendar.CalendarUnavailable:return jsonify(error='Agenda indisponible. Réessayez.'),503
