"""Anonymous aggregate public page views; no visitor identifiers stored."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import server

def today():return datetime.now(ZoneInfo('Europe/Paris')).date()
def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS site_page_views(day TEXT PRIMARY KEY,views INTEGER NOT NULL)')
def record():
    with server.connect() as db:
        schema(db)
        db.execute('INSERT INTO site_page_views(day,views) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET views=site_page_views.views+1',(today().isoformat(),))
def summary():
    day=today()
    with server.connect() as db:
        schema(db)
        rows=db.execute('SELECT day,views FROM site_page_views').fetchall()
    return dict(total=sum(v for d,v in rows),today=sum(v for d,v in rows if d==day.isoformat()),week=sum(v for d,v in rows if (day-timedelta(days=6)).isoformat()<=d<=day.isoformat()),since=min((d for d,v in rows),default=None))
