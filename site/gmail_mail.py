"""Send-only Gmail OAuth and durable cancellation notifications. No mailbox reading."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from cryptography.fernet import Fernet
from flask import Blueprint, jsonify, redirect, request
import payments
import server
import admin

bp = Blueprint('gmail_mail', __name__)
OWNER = 'eventsbooth360@gmail.com'
SCOPE = 'https://www.googleapis.com/auth/gmail.send'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
COOKIE = 'eventsbooth_gmail_state'

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS gmail_connection(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS gmail_states(state_hash TEXT PRIMARY KEY,expires INTEGER NOT NULL,verifier TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS cancellation_mail(booking_id TEXT NOT NULL,recipient TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,message_id TEXT,PRIMARY KEY(booking_id,recipient))')

def configured():
    c = payments.config()
    return bool(c.get('GMAIL_CLIENT_ID') and c.get('GMAIL_CLIENT_SECRET'))

def cipher():
    # Separate purpose-derived key; changing the client secret requires reconnecting Gmail.
    secret = payments.config().get('GMAIL_CLIENT_SECRET', '')
    if not secret: raise RuntimeError('Gmail non configuré.')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('gmail-token-v1:' + secret).encode()).digest()))

def callback_url():
    return payments.config()['SITE_URL'].rstrip('/') + '/api/admin/gmail/callback'

def token_request(values):
    c = payments.config()
    data = urlencode({**values, 'client_id': c['GMAIL_CLIENT_ID'], 'client_secret': c['GMAIL_CLIENT_SECRET']}).encode()
    with urlopen(Request(TOKEN_URL, data=data), timeout=15) as response:
        return json.load(response)

def connected():
    if not configured(): return False
    with server.connect() as db:
        schema(db)
        row = db.execute("SELECT value FROM gmail_connection WHERE key='refresh'").fetchone()
    if not row: return False
    try: return bool(cipher().decrypt(row[0].encode()))
    except Exception: return False

@bp.get('/api/admin/gmail/status')
@admin.protected
def status():
    return jsonify(configured=configured(), connected=connected(), sender=OWNER)

@bp.post('/api/admin/gmail/connect')
@admin.protected
def connect():
    if not configured(): return jsonify(error='Ajoutez GMAIL_CLIENT_ID et GMAIL_CLIENT_SECRET dans Render.'), 503
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    with server.connect() as db:
        schema(db)
        db.execute('DELETE FROM gmail_states WHERE expires < ?', (int(time.time()),))
        db.execute('INSERT INTO gmail_states VALUES(?,?,?)', (hashlib.sha256(state.encode()).hexdigest(), int(time.time()) + 600, verifier))
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': payments.config()['GMAIL_CLIENT_ID'], 'redirect_uri': callback_url(),
        'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline',
        'prompt': 'consent', 'login_hint': OWNER, 'state': state,
        'code_challenge': challenge, 'code_challenge_method': 'S256'})
    response = jsonify(url=url)
    response.set_cookie(COOKIE, state, max_age=600, secure=callback_url().startswith('https://'), httponly=True, samesite='Lax', path='/api/admin/gmail/callback')
    return response

@bp.get('/api/admin/gmail/callback')
def callback():
    # The admin Strict cookie is intentionally absent on a cross-site callback.
    # A short-lived Lax cookie and single-use DB state bind this to the initiating browser.
    state = request.args.get('state', '')
    cookie = request.cookies.get(COOKIE, '')
    if not state or len(state) > 200 or not cookie or not hmac.compare_digest(state, cookie):
        return 'Connexion Gmail refusée. Recommencez depuis /admin.', 400
    with server.connect() as db:
        schema(db); db.execute('BEGIN IMMEDIATE')
        digest = hashlib.sha256(state.encode()).hexdigest()
        row = db.execute('SELECT expires,verifier FROM gmail_states WHERE state_hash=?', (digest,)).fetchone()
        db.execute('DELETE FROM gmail_states WHERE state_hash=?', (digest,))
    result = 'error'
    if row and row[0] >= time.time() and not request.args.get('error') and request.args.get('code'):
        try:
            tokens = token_request({'grant_type': 'authorization_code', 'code': request.args['code'], 'redirect_uri': callback_url(), 'code_verifier': row[1]})
            if SCOPE not in tokens.get('scope', '').split() or not tokens.get('refresh_token'): raise RuntimeError('Autorisation incomplète.')
            encrypted = cipher().encrypt(tokens['refresh_token'].encode()).decode()
            with server.connect() as db:
                db.execute("INSERT INTO gmail_connection VALUES('refresh',?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (encrypted,))
            result = 'connected'
        except Exception: pass  # Never expose Google's response, auth codes or tokens.
    response = redirect('/admin?gmail=' + result)
    response.delete_cookie(COOKIE, path='/api/admin/gmail/callback')
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response

def access_token():
    with server.connect() as db:
        schema(db)
        row = db.execute("SELECT value FROM gmail_connection WHERE key='refresh'").fetchone()
    if not row: raise RuntimeError('Connectez Gmail dans l’administration.')
    refresh = cipher().decrypt(row[0].encode()).decode()
    return token_request({'grant_type': 'refresh_token', 'refresh_token': refresh})['access_token']

def send(recipient, subject, body, token):
    message = EmailMessage()
    message['From'] = 'EventsBooth360 <' + OWNER + '>'
    message['To'] = recipient
    message['Subject'] = subject
    message['Reply-To'] = OWNER
    message.set_content(body)
    payload = json.dumps({'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}).encode()
    req = Request('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', data=payload,
                  headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urlopen(req, timeout=20) as response:
        return json.load(response)['id']

@bp.post('/api/admin/gmail/test')
@admin.protected
def test():
    try:
        send(OWNER, 'EventsBooth360 — test des mails du site', 'La connexion Gmail du site fonctionne. Ce message de test ne correspond à aucune réservation et ne déclenche aucun paiement ni remboursement.', access_token())
        return jsonify(ok=True)
    except Exception: return jsonify(error='Envoi non confirmé. Vérifiez Gmail avant de réessayer ou reconnectez le compte.'), 503

@bp.post('/api/admin/gmail/pending')
@admin.protected
def pending():
    data = request.get_json(silent=True) or {}
    bid = data.get('booking_id')
    if not isinstance(bid,str) or len(bid)>100: return jsonify(error='Référence invalide.'),400
    deliver(bid)
    return jsonify(ok=True)

def valid_email(value):
    from email.utils import parseaddr
    return isinstance(value, str) and len(value) <= 254 and not any(c.isspace() for c in value) and '@' in value and parseaddr(value)[1] == value

def queue_cancellation(db, bid, customer_email=None):
    """Called in the same transaction as the successful cancellation; never backfills old ones."""
    schema(db)
    row = db.execute('SELECT b.event_address,b.total,b.due,a.start,a.end,a.refund_status,a.refunded,c.first_name,c.last_name,c.phone,p.event_type,p.video_name FROM bookings b JOIN admin_cancellations a ON a.booking_id=b.id LEFT JOIN booking_contacts c ON c.booking_id=b.id LEFT JOIN booking_personalization p ON p.booking_id=b.id WHERE b.id=?', (bid,)).fetchone()
    if not row: return
    address,total,due,start,end,refund,amount,first,last,phone,event,theme = row
    euros = lambda n: f'{n / 100:.2f} €'.replace('.', ',')
    when = datetime.fromisoformat(start).strftime('%d/%m/%Y à %H:%M') + ' — ' + datetime.fromisoformat(end).strftime('%d/%m/%Y à %H:%M') + ' (heure de Paris)'
    refund_text = {
        'succeeded': 'Remboursement effectué via Stripe : ' + euros(amount) + '. Le délai de réception dépend de votre banque.',
        'pending': 'Remboursement demandé via Stripe : ' + euros(amount) + '. Il est encore en cours de traitement.',
        'already_refunded': 'Le paiement avait déjà été remboursé : ' + euros(amount) + '.',
        'no_payment': 'Aucun paiement en ligne à rembourser.'}.get(refund, 'Statut du remboursement à vérifier auprès d’EventsBooth360.')
    details = '\n'.join([
        'La réservation suivante est annulée.', '', 'Référence : ' + bid,
        'Client : ' + ' '.join(filter(None, [first,last])), 'Téléphone : ' + (phone or 'Non renseigné'),
        'Événement : ' + (event or 'Non renseigné'), 'Dates et horaires : ' + when,
        'Lieu de livraison / événement : ' + address, 'Personnalisation vidéo : ' + (theme or 'Non renseignée'),
        'Prix de la formule : ' + euros(total), 'Montant payé en ligne : ' + euros(due), '', refund_text,
        '', 'Les éventuels paiements effectués séparément sont à vérifier directement avec EventsBooth360.',
        '', 'Pour toute question, répondez à ce message.', 'EventsBooth360 — 06 11 57 46 64', 'https://eventsbooth360.fr'])
    recipients = {OWNER}
    if valid_email(customer_email): recipients.add(customer_email)
    for recipient in recipients:
        body = details
        if recipient == OWNER and not valid_email(customer_email): body += '\n\nAttention : adresse e-mail client indisponible ; aucun mail n’a pu être préparé pour le client.'
        db.execute("INSERT INTO cancellation_mail(booking_id,recipient,subject,body,status) VALUES(?,?,?,?,'pending') ON CONFLICT(booking_id,recipient) DO NOTHING", (bid,recipient,'Annulation de votre réservation — EventsBooth360',body))

def deliver(bid):
    """Never retries an ambiguous send: sending/unknown require a mailbox check."""
    try: token = access_token()
    except Exception: return
    with server.connect() as db:
        schema(db)
        rows = db.execute("SELECT recipient,subject,body FROM cancellation_mail WHERE booking_id=? AND status='pending'", (bid,)).fetchall()
    for recipient,subject,body in rows:
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT status FROM cancellation_mail WHERE booking_id=? AND recipient=?', (bid,recipient)).fetchone()
            if not current or current[0] != 'pending': continue
            db.execute("UPDATE cancellation_mail SET status='sending' WHERE booking_id=? AND recipient=?", (bid,recipient))
        status, ident = 'unknown', None
        try: ident = send(recipient,subject,body,token); status = 'sent'
        except HTTPError: status = 'failed'
        except Exception: pass
        with server.connect() as db:
            db.execute('UPDATE cancellation_mail SET status=?,message_id=? WHERE booking_id=? AND recipient=?', (status,ident,bid,recipient))
