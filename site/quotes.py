"""Private estimates: immutable PDF snapshots and at-most-once mail attempts."""
import base64,json,secrets,time
from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from datetime import datetime,timedelta
from flask import request,jsonify,Response
from admin import bp,protected
import server,gmail_mail,travel

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS admin_quotes(id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, number INTEGER UNIQUE NOT NULL, data TEXT NOT NULL, pdf TEXT NOT NULL, created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS quote_mail(quote_id TEXT NOT NULL, recipient TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT, PRIMARY KEY(quote_id,recipient))')

def amounts(hours,discount,fee):
    h=Decimal(str(hours));d=Decimal(str(discount))
    if not h.is_finite() or not d.is_finite() or h>24 or h not in (2,3,4) and (h<4 or h*2!=int(h*2)) or not 0<=d<=100:raise ValueError()
    base={Decimal(2):22900,Decimal(3):29900,Decimal(4):35900}.get(h)
    if base is None:base=int((h*8975).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    reduction=int((Decimal(base)*d/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    total=base-reduction+fee
    deposit=int((Decimal(total)*Decimal('.30')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return dict(base=base,reduction=reduction,total=total,deposit=deposit,balance=total-deposit)

def clean(data):
    result={}
    for key,limit in [('name',160),('email',254),('phone',30),('address',500),('event',120),('notes',1500)]:
        value=data.get(key,'')
        if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c!='\n' for c in value):raise ValueError()
        result[key]=value.strip()
    if not all(result[k] for k in ('name','email','phone','address','event')) or not gmail_mail.valid_email(result['email']):raise ValueError()
    start=datetime.fromisoformat(data['date']+'T'+data['time'])
    if start.tzinfo or start.date()<datetime.now().date():raise ValueError()
    trip=travel.verify(data.get('travel_token',''),result['address'])
    fee=int(trip['fee_cents'])
    result.update(amounts(data['hours'],data['discount'],fee))
    result.update(hours=str(data['hours']),discount=str(data['discount']),start=start.isoformat(),end=(start+timedelta(hours=float(data['hours']))).isoformat(),travel_fee=fee,travel_km=trip['round_trip_km'],travel_label=trip['label'],issued=datetime.now().date().isoformat(),valid_until=(datetime.now()+timedelta(days=30)).date().isoformat())
    return result

def deliver(qid):
    try:token=gmail_mail.access_token()
    except Exception:return
    with server.connect() as db:
        schema(db)
        row=db.execute('SELECT number,data,pdf FROM admin_quotes WHERE id=?',(qid,)).fetchone()
        if not row:return
        pending=db.execute("SELECT recipient FROM quote_mail WHERE quote_id=? AND status='pending'",(qid,)).fetchall()
    number,data,pdf=row;data=json.loads(data)
    for (recipient,) in pending:
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT status FROM quote_mail WHERE quote_id=? AND recipient=?',(qid,recipient)).fetchone()
            if not state or state[0]!='pending':continue
            db.execute("UPDATE quote_mail SET status='sending' WHERE quote_id=? AND recipient=?",(qid,recipient))
        status='unknown';mid=None
        try:
            body=f"Bonjour,\n\nVous trouverez en pièce jointe notre proposition DEV-{number:04d} pour votre événement.\nDurée : {data['hours']} h. Total proposé : {data['total']/100:.2f} EUR, déplacement compris.\nCe document ne confirme pas une réservation et aucun créneau n'est bloqué. Contactez-nous pour valider votre demande.\n\nEventsBooth360"
            mid=gmail_mail.send(recipient,f'Votre proposition DEV-{number:04d} - EventsBooth360',body,token,(f'Devis-DEV-{number:04d}.pdf',base64.b64decode(pdf)))
            status='sent'
        except Exception:pass
        with server.connect() as db:db.execute('UPDATE quote_mail SET status=?,message_id=? WHERE quote_id=? AND recipient=?',(status,mid,qid,recipient))

@bp.post('/api/admin/quotes/travel')
@protected
def quote_travel():
    try:return jsonify(travel.quote((request.get_json(silent=True) or {}).get('address','')))
    except travel.TravelError as e:return jsonify(error=str(e)),400

@bp.get('/api/admin/quotes')
@protected
def listing():
    with server.connect() as db:
        schema(db)
        rows=db.execute('SELECT id,number,data FROM admin_quotes ORDER BY number DESC LIMIT 50').fetchall()
        result=[]
        for qid,number,raw in rows:
            data=json.loads(raw)
            mails=[dict(recipient=r,status=s) for r,s in db.execute('SELECT recipient,status FROM quote_mail WHERE quote_id=?',(qid,)).fetchall()]
            result.append(dict(id=qid,number=number,name=data['name'],total=data['total'],issued=data['issued'],emails=mails))
    return jsonify(quotes=result)

@bp.post('/api/admin/quotes')
@protected
def create():
    payload=request.get_json(silent=True) or {};key=payload.get('request_key','')
    if not isinstance(key,str) or not 16<=len(key)<=80:return jsonify(error='Identifiant de demande invalide.'),400
    with server.connect() as db:
        schema(db)
        previous=db.execute('SELECT id,number FROM admin_quotes WHERE request_key=?',(key,)).fetchone()
    if previous:return jsonify(ok=True,id=previous[0],number=previous[1])
    try:data=clean(payload)
    except travel.TravelError as e:return jsonify(error=str(e)),400
    except (ValueError,KeyError,TypeError,InvalidOperation,OverflowError):return jsonify(error='Vérifiez les coordonnées, la durée, la remise et la date.'),400
    from quote_pdf import render
    with server.connect() as db:
        schema(db);db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT id,number FROM admin_quotes WHERE request_key=?',(key,)).fetchone()
        if previous:return jsonify(ok=True,id=previous[0],number=previous[1])
        number=db.execute('SELECT COALESCE(MAX(number),10)+1 FROM admin_quotes').fetchone()[0]
        qid=secrets.token_hex(16);pdf=base64.b64encode(render(number,data)).decode()
        db.execute('INSERT INTO admin_quotes VALUES(?,?,?,?,?,?)',(qid,key,number,json.dumps(data,ensure_ascii=False),pdf,int(time.time())))
        for recipient in {data['email'].casefold(),gmail_mail.OWNER.casefold()}:
            db.execute("INSERT INTO quote_mail VALUES(?,?,'pending',NULL)",(qid,recipient))
    deliver(qid)
    return jsonify(ok=True,id=qid,number=number)

@bp.post('/api/admin/quotes/<qid>/send')
@protected
def send_pending(qid):
    deliver(qid)
    return jsonify(ok=True)

@bp.get('/api/admin/quotes/<qid>/pdf')
@protected
def download(qid):
    with server.connect() as db:
        schema(db);row=db.execute('SELECT number,pdf FROM admin_quotes WHERE id=?',(qid,)).fetchone()
    if not row:return jsonify(error='Devis introuvable.'),404
    return Response(base64.b64decode(row[1]),mimetype='application/pdf',headers={'Content-Disposition':f'attachment; filename="Devis-DEV-{row[0]:04d}.pdf"','Cache-Control':'no-store'})
