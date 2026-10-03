"""Production WSGI entrypoint for Render. Only public/ is served as static content."""
import calendar
from datetime import date,timedelta
from pathlib import Path
from flask import Flask,request,jsonify,send_from_directory
import payments,server,apple_calendar
ROOT=Path(__file__).resolve().parent
app=Flask(__name__,static_folder=str(ROOT/'public'),static_url_path='/assets')
app.config['MAX_CONTENT_LENGTH']=65536
@app.after_request
def security(response):
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    if request.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
    return response
@app.get('/health')
def health():return jsonify(status='ok')
@app.get('/')
def home():return send_from_directory(ROOT/'public','index.html')
@app.get('/<path:filename>')
def files(filename):return send_from_directory(ROOT/'public',filename)
@app.get('/api/payment-status')
def payment_status():
    enabled=False
    if payments.ready() and payments.config().get('DATABASE_URL') and (not apple_calendar.enabled() or apple_calendar.configured()):
        try:
            with server.connect() as db:enabled=server.setting(db,'ready','false')=='true'
        except Exception:pass
    return jsonify(enabled=enabled,test_mode=not payments.live_mode())
@app.get('/api/availability')
def availability():
    try:
        year,month=map(int,request.args['month'].split('-'));duration=int(request.args.get('duration','2'))
        if duration not in (2,3,4) or not 2026<=year<=2100:raise ValueError()
        count=calendar.monthrange(year,month)[1]
    except (ValueError,KeyError):return jsonify(error='Paramètres invalides.'),400
    items={};ready=False
    if payments.config().get('DATABASE_URL'):
        try:
            external=apple_calendar.busy(date(year,month,1),date(year,month,count)+timedelta(days=1))
            try:apple_calendar.export_pending(server.connect)
            except apple_calendar.CalendarUnavailable as e:app.logger.warning('Export iCloud en attente de reprise: %s',str(e))
            with server.connect() as db:
                ready=server.setting(db,'ready','false')=='true'
                for n in range(1,count+1):
                    day=date(year,month,n);slots=server.openings(db,day,duration,extra=external) if ready else []
                    items[day.isoformat()]={'state':('free' if len(slots)==(13-duration)*2+1 else 'partial' if slots else 'full') if ready else 'unknown','slots':slots}
        except apple_calendar.CalendarUnavailable as e:
            app.logger.warning('Agenda Apple: %s',str(e))
            return jsonify(error='Agenda Apple temporairement indisponible. Réessayez dans quelques instants.'),503
        except Exception:return jsonify(error='Planning temporairement indisponible.'),503
    else:
        items={date(year,month,n).isoformat():{'state':'unknown','slots':[]} for n in range(1,count+1)}
    return jsonify(ready=ready,timezone='Europe/Paris',days=items,test_mode=False)
@app.post('/api/checkout')
def checkout():
    if not payments.config().get('DATABASE_URL'):return jsonify(error='Planning persistant non connecté.'),503
    origin=payments.config().get('SITE_URL','').rstrip('/')
    if not origin or request.headers.get('Origin')!=origin:return jsonify(error='Origine refusée.'),403
    try:return jsonify(payments.checkout(request.get_json(),server.connect,server.openings,server.setting))
    except payments.PaymentError as e:return jsonify(error=str(e)),400
    except Exception:return jsonify(error='Service temporairement indisponible.'),503
@app.post('/api/stripe/webhook')
def webhook():
    if not payments.config().get('DATABASE_URL'):return jsonify(error='Planning persistant non connecté.'),503
    try:
        payments.webhook(request.get_data(),request.headers.get('Stripe-Signature',''),server.connect)
        # DB confirmation is durable before exporting. A 503 makes Stripe retry;
        # the idempotent webhook then retries the pending export without a new charge.
        apple_calendar.export_pending(server.connect)
        return jsonify(received=True)
    except payments.PaymentError as e:return jsonify(error=str(e)),400
    except Exception:return jsonify(error='Traitement temporairement indisponible.'),503
@app.errorhandler(404)
def missing(e):return 'Page introuvable. <a href="/">Revenir à EventsBooth360</a>',404
