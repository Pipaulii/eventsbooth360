"""Private iCloud CalDAV bridge. The booking database remains authoritative."""
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, quote
import threading
import time as clock
import payments

PARIS = ZoneInfo('Europe/Paris')
_cache = {}
_cache_lock = threading.Lock()
_deletion_checks = {}

class CalendarUnavailable(Exception):
    pass

def enabled():
    return payments.config().get('ICLOUD_SYNC_ENABLED', 'false').lower() == 'true'

def configured():
    c = payments.config()
    return bool(c.get('ICLOUD_USERNAME') and c.get('ICLOUD_APP_PASSWORD'))

@contextmanager
def calendar_connection():
    if not configured():
        raise CalendarUnavailable('Accès iCloud incomplet.')
    import caldav
    c = payments.config()
    try:
        with caldav.DAVClient(url='https://caldav.icloud.com',
                username=c['ICLOUD_USERNAME'], password=c['ICLOUD_APP_PASSWORD'],
                timeout=10) as client:
            expected = c.get('ICLOUD_CALENDAR_NAME', 'EventsBooth360').strip().casefold()
            calendars = [cal for cal in client.principal().calendars()
                         if cal.get_display_name().strip().casefold() == expected]
            if len(calendars) != 1:
                raise CalendarUnavailable('Calendrier EventsBooth360 absent ou ambigu.')
            cal = calendars[0]
            url = urlparse(str(cal.url))
            if url.scheme != 'https' or not (url.hostname or '').endswith('.icloud.com'):
                raise CalendarUnavailable('Adresse iCloud invalide.')
            yield cal
    except CalendarUnavailable:
        raise
    except Exception as exc:
        # Never expose CalDAV exceptions: they may contain credentials or event data.
        raise CalendarUnavailable('Synchronisation iCloud temporairement indisponible (' + type(exc).__name__ + ').') from None

def local(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=PARIS)
        return value.astimezone(PARIS).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raise CalendarUnavailable('Horaire iCloud invalide.')

def intervals(resources, lower, upper):
    """Expanded recurrence resources; store only busy times, never event titles."""
    result = []
    for resource in resources:
        for event in resource.icalendar_instance.walk('VEVENT'):
            if str(event.get('STATUS', '')).upper() == 'CANCELLED':
                continue
            if str(event.get('TRANSP', 'OPAQUE')).upper() == 'TRANSPARENT':
                continue
            if 'RRULE' in event:
                # Refuse an unexpanded recurrence rather than offer false availability.
                raise CalendarUnavailable('Récurrence iCloud non développée.')
            start_value = event.decoded('DTSTART')
            begin = local(start_value)
            if 'DTEND' in event:
                finish = local(event.decoded('DTEND'))
            elif 'DURATION' in event:
                finish = begin + event.decoded('DURATION')
            elif isinstance(start_value, date) and not isinstance(start_value, datetime):
                finish = begin + timedelta(days=1)
            else:
                # A zero-duration calendar reminder does not block an interval.
                continue
            if finish <= begin:
                raise CalendarUnavailable('Période iCloud invalide.')
            if begin < upper and finish > lower:
                result.append((begin.isoformat(), finish.isoformat()))
    return result

def busy(first_day, last_day, fresh=False):
    """last_day is exclusive. Public views cache 60s; Checkout always reads fresh."""
    if not enabled():
        return []
    lower = datetime.combine(first_day, time.min) - timedelta(minutes=120)
    upper = datetime.combine(last_day, time.min) + timedelta(hours=5)
    c = payments.config()
    key = (c.get('ICLOUD_USERNAME'), c.get('ICLOUD_CALENDAR_NAME', 'EventsBooth360'),
           first_day.isoformat(), last_day.isoformat())
    with _cache_lock:
        cached = _cache.get(key)
        if not fresh and cached and clock.monotonic() - cached[0] < 60:
            return list(cached[1])
    with calendar_connection() as cal:
        resources = cal.search(start=lower.replace(tzinfo=PARIS),
            end=upper.replace(tzinfo=PARIS), event=True, expand=True)
        result = intervals(resources, lower, upper)
    with _cache_lock:
        if len(_cache) >= 24:
            _cache.clear()
        _cache[key] = (clock.monotonic(), result)
    return list(result)

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS calendar_exports(booking_id TEXT PRIMARY KEY, exported INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS calendar_missing(booking_id TEXT PRIMARY KEY, first_seen INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS calendar_cancellations(booking_id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, cancelled_at INTEGER NOT NULL)')

def reconcile_deletions(connect, first_day, last_day):
    """Only cancel exported events after two successful missing checks 120s apart.

    Preserve booking and original times for review; never refund a payment.
    A CalDAV timeout, authentication error or failed search cancels nothing.
    """
    if not enabled():return
    check_key=(first_day.isoformat(),last_day.isoformat())
    with _cache_lock:
        if clock.monotonic()-_deletion_checks.get(check_key,float('-inf'))<120:return
        _deletion_checks[check_key]=clock.monotonic()
    from caldav.lib.error import NotFoundError
    lower=datetime.combine(first_day,time.min).isoformat()
    upper=datetime.combine(last_day,time.min).isoformat()
    now=int(clock.time())
    with connect() as db:
        payments.schema(db);schema(db)
        rows=db.execute("SELECT b.id,s.start,s.end FROM bookings b JOIN slots s ON s.id=b.slot_id JOIN calendar_exports c ON c.booking_id=b.id WHERE b.status='confirmed' AND c.exported=1 AND s.start < ? AND s.end > ?",(upper,lower)).fetchall()
    if not rows:return
    observations=[]
    # Complete all reads before mutating the database: a partial outage cannot
    # turn an incomplete calendar response into cancellations.
    with calendar_connection() as cal:
        for booking_id,begin,finish in rows:
            uid='eventsbooth360-'+booking_id+'@eventsbooth360.fr'
            try:
                cal.event_by_url(str(cal.url).rstrip('/')+'/'+quote(uid,safe='')+'.ics')
                missing=False
            except NotFoundError:missing=True
            observations.append((booking_id,begin,finish,missing))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for booking_id,begin,finish,missing in observations:
            if not missing:
                db.execute('DELETE FROM calendar_missing WHERE booking_id=?',(booking_id,));continue
            row=db.execute('SELECT first_seen FROM calendar_missing WHERE booking_id=?',(booking_id,)).fetchone()
            if not row:
                db.execute('INSERT INTO calendar_missing VALUES(?,?) ON CONFLICT(booking_id) DO NOTHING',(booking_id,now));continue
            if now-row[0]<120:continue
            booking=db.execute("SELECT slot_id FROM bookings WHERE id=? AND status='confirmed'",(booking_id,)).fetchone()
            if not booking:continue
            db.execute('INSERT INTO calendar_cancellations VALUES(?,?,?,?) ON CONFLICT(booking_id) DO NOTHING',(booking_id,begin,finish,now))
            db.execute("UPDATE bookings SET status='cancelled_calendar' WHERE id=?",(booking_id,))
            db.execute('DELETE FROM slots WHERE id=?',(booking[0],))
            db.execute('DELETE FROM calendar_missing WHERE booking_id=?',(booking_id,))
    with _cache_lock:_cache.clear()

def event_data(booking_id, begin, finish):
    from icalendar import Calendar, Event
    start = datetime.fromisoformat(begin).replace(tzinfo=PARIS)
    end = datetime.fromisoformat(finish).replace(tzinfo=PARIS)
    hours = int((end - start).total_seconds() / 3600)
    content = Calendar()
    content.add('prodid', '-//EventsBooth360//Réservations//FR')
    content.add('version', '2.0')
    event = Event()
    event.add('uid', 'eventsbooth360-' + booking_id + '@eventsbooth360.fr')
    event.add('dtstamp', datetime.now(timezone.utc))
    # UTC avoids dependence on a server's VTIMEZONE implementation.
    event.add('dtstart', start.astimezone(timezone.utc))
    event.add('dtend', end.astimezone(timezone.utc))
    event.add('summary', f'EventsBooth360 — location confirmée — {hours} h')
    event.add('description', 'Réservation ' + booking_id +
              '\nSupprimer cet événement libère le créneau sur le site après vérification. Aucun remboursement automatique. Modifier ses horaires ne déplace pas la réservation du site.')
    event.add('status', 'CONFIRMED')
    event.add('transp', 'OPAQUE')
    event.add('class', 'PRIVATE')
    content.add_component(event)
    return content.to_ical().decode('utf-8')

def export_pending(connect):
    """Durable queue + stable UIDs. Retry on webhooks and subsequent public visits."""
    if not enabled():
        return
    from caldav.lib.error import NotFoundError
    with connect() as db:
        payments.schema(db)
        schema(db)
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO calendar_exports(booking_id) SELECT id FROM bookings WHERE status='confirmed' ON CONFLICT(booking_id) DO NOTHING")
        rows = db.execute("SELECT b.id,s.start,s.end FROM bookings b JOIN slots s ON s.id=b.slot_id JOIN calendar_exports c ON c.booking_id=b.id WHERE b.status='confirmed' AND c.exported=0 ORDER BY b.created LIMIT 5").fetchall()
        if not rows:
            return
        with calendar_connection() as cal:
            for booking_id, begin, finish in rows:
                uid = 'eventsbooth360-' + booking_id + '@eventsbooth360.fr'
                try:
                    # iCloud can reject UID-filtered REPORT queries. Use the
                    # deterministic resource URL for idempotent GET instead.
                    cal.event_by_url(str(cal.url).rstrip('/') + '/' + quote(uid, safe='') + '.ics')
                except NotFoundError:
                    cal.add_event(event_data(booking_id, begin, finish))
                db.execute('UPDATE calendar_exports SET exported=1 WHERE booking_id=?', (booking_id,))
    with _cache_lock:
        _cache.clear()

def remove_booking(booking_id):
    if not enabled():return
    from caldav.lib.error import NotFoundError
    uid='eventsbooth360-'+booking_id+'@eventsbooth360.fr'
    with calendar_connection() as cal:
        try:cal.event_by_url(str(cal.url).rstrip('/')+'/'+quote(uid,safe='')+'.ics').delete()
        except NotFoundError:pass
    with _cache_lock:_cache.clear()
