"""Serveur local EventsBooth360 : calendrier public sans données clients."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from datetime import date, datetime, timedelta
import argparse, json, sqlite3, calendar
import payments
from storage import PostgresConnection
ROOT=Path(__file__).resolve().parent
DATABASE=ROOT/'planning.sqlite3'
class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()
def connect():
    url=payments.config().get('DATABASE_URL','')
    if payments.config().get('RENDER') and not url:raise RuntimeError('Base persistante non configuree.')
    db=PostgresConnection(url) if url else sqlite3.connect(DATABASE,timeout=10,factory=ClosingConnection)
    if url:db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, CHECK(end>start))')
    db.execute('CREATE INDEX IF NOT EXISTS slots_dates ON slots(start,end)')
    db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.commit()
    return db
def setting(db,key,default):
    row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return row[0] if row else default
def openings(db,day,duration,extra=()):
    start=datetime.combine(day,datetime.min.time()).replace(hour=10)
    close=start.replace(hour=23)
    margin=timedelta(minutes=30)
    occupied=db.execute('SELECT start,end FROM slots WHERE start < ? AND end > ?',((close+margin).isoformat(),(start-margin).isoformat())).fetchall()
    occupied=list(occupied)+list(extra)
    result=[]
    while start+timedelta(hours=duration)<=close:
        end=start+timedelta(hours=duration)
        if not any(start-margin<datetime.fromisoformat(b) and end+margin>datetime.fromisoformat(a) for a,b in occupied):
            result.append(start.strftime('%H:%M'))
        start+=timedelta(minutes=30)
    return result
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'public'),**kwargs)
    def end_headers(self):
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        self.send_header('X-Content-Type-Options','nosniff')
        super().end_headers()
    def respond(self,status,value):
        body=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>65536: return self.respond(400,{'error':'Requête invalide.'})
            payload=self.rfile.read(size)
            if self.path=='/api/stripe/webhook':
                payments.webhook(payload,self.headers.get('Stripe-Signature',''),connect);return self.respond(200,{'received':True})
            if self.path!='/api/checkout':return self.respond(404,{'error':'Introuvable.'})
            expected=payments.config().get('SITE_URL','http://127.0.0.1:3600').rstrip('/')
            if self.headers.get('Origin')!=expected:return self.respond(403,{'error':'Origine refusée.'})
            if payments.live_mode() and DATABASE.name=='planning-test.sqlite3':return self.respond(409,{'error':'Planning de test incompatible avec les paiements réels.'})
            return self.respond(200,payments.checkout(json.loads(payload),connect,openings,setting))
        except payments.PaymentError as e:return self.respond(400,{'error':str(e)})
        except (ValueError,TypeError):return self.respond(400,{'error':'Requête invalide.'})
        except Exception:return self.respond(503,{'error':'Service temporairement indisponible.'})
    def do_GET(self):
        from urllib.parse import urlparse,parse_qs
        parsed=urlparse(self.path)
        if parsed.path=='/api/payment-status':
            with connect() as db: enabled=payments.ready() and setting(db,'ready','false')=='true'
            return self.respond(200,{'enabled':enabled,'test_mode':not payments.live_mode()})
        if parsed.path!='/api/availability':return super().do_GET()
        try:
            q=parse_qs(parsed.query);year,month=map(int,q['month'][0].split('-'));duration=int(q.get('duration',['2'])[0])
            if duration not in (2,3,4) or not 2026<=year<=2100:raise ValueError()
            with connect() as db:
                ready=setting(db,'ready','false')=='true';items={}
                for n in range(1,calendar.monthrange(year,month)[1]+1):
                    day=date(year,month,n);available=openings(db,day,duration) if ready else []
                    max_slots= len(range(0,(13-duration)*2+1))
                    items[day.isoformat()]={'state':('free' if len(available)==max_slots else 'partial' if available else 'full') if ready else 'unknown','slots':available}
            body=json.dumps({'ready':ready,'timezone':'Europe/Paris','days':items,'test_mode':DATABASE.name=='planning-test.sqlite3'}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except (ValueError,KeyError,IndexError):self.send_error(400,'Parametres invalides')
if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command');serve=sub.add_parser('serve');serve.add_argument('--port',type=int,default=3600);serve.add_argument('--test',action='store_true')
    block=sub.add_parser('block');block.add_argument('start');block.add_argument('end');sub.add_parser('list');unblock=sub.add_parser('unblock');unblock.add_argument('id',type=int);sub.add_parser('activate');sub.add_parser('deactivate');args=p.parse_args()
    if args.command in (None,'serve'):
        if getattr(args,'test',False):
            DATABASE=ROOT/'planning-test.sqlite3'
            with connect() as db:db.execute("INSERT OR REPLACE INTO settings VALUES('ready','true')")
        connect().close();print('EventsBooth360 : http://127.0.0.1:'+str(getattr(args,'port',3600)),flush=True);ThreadingHTTPServer(('127.0.0.1',getattr(args,'port',3600)),Handler).serve_forever()
    else:
        with connect() as db:
            if args.command=='block':
                start=datetime.fromisoformat(args.start);end=datetime.fromisoformat(args.end)
                if start.tzinfo or end.tzinfo or end<=start:raise SystemExit('Utiliser des horaires locaux Europe/Paris, avec une fin apres le debut.')
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM slots WHERE start < ? AND end > ?',(end.isoformat(),start.isoformat())).fetchone():raise SystemExit('Ce creneau recoupe une plage deja bloquee.')
                cur=db.execute('INSERT INTO slots(start,end) VALUES(?,?)',(start.isoformat(),end.isoformat()));print('Plage bloquee :',cur.lastrowid)
            elif args.command=='list':
                for row in db.execute('SELECT id,start,end FROM slots ORDER BY start'):print(*row)
            elif args.command=='unblock':db.execute('DELETE FROM slots WHERE id=?',(args.id,))
            else:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ready','true' if args.command=='activate' else 'false'))
