import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import payments,server,apple_calendar as apple
from icalendar import Calendar,Event
from caldav.lib.error import NotFoundError

def resource(start,end=None,**props):
    cal=Calendar();event=Event();event.add('dtstart',start)
    if end is not None:event.add('dtend',end)
    for key,value in props.items():event.add(key,value)
    cal.add_component(event)
    return SimpleNamespace(icalendar_instance=cal)

class CalendarTests(unittest.TestCase):
    def test_calendar_deletion_grace_outage_and_durable_cancellation(self):
        apple._deletion_checks.clear()
        from contextlib import contextmanager
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'DATABASE',Path(folder)/'test.db'),patch.object(payments,'config',return_value={'ICLOUD_SYNC_ENABLED':'true'}):
            with server.connect() as db:
                payments.schema(db);apple.schema(db)
                slot=db.execute('INSERT INTO slots(start,end) VALUES(?,?)',('2026-10-06T14:00:00','2026-10-06T16:00:00')).lastrowid
                db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',('test-delete',slot,22900,6870,'confirmed','cs_test','private address',1))
                db.execute('INSERT INTO calendar_exports VALUES(?,1)',('test-delete',))
            class Remote:
                url='https://example.icloud.com/calendar/'
                def event_by_url(self,url):raise NotFoundError()
            @contextmanager
            def connection():yield Remote()
            with patch.object(apple,'calendar_connection',connection),patch.object(apple.clock,'time',return_value=1000):
                apple.reconcile_deletions(server.connect,date(2026,10,1),date(2026,11,1))
            apple._deletion_checks.clear()
            with patch.object(apple,'calendar_connection',side_effect=apple.CalendarUnavailable('offline')),patch.object(apple.clock,'time',return_value=1121):
                with self.assertRaises(apple.CalendarUnavailable):apple.reconcile_deletions(server.connect,date(2026,10,1),date(2026,11,1))
            with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0],1)
            apple._deletion_checks.clear()
            with patch.object(apple,'calendar_connection',connection),patch.object(apple.clock,'time',return_value=1121):
                apple.reconcile_deletions(server.connect,date(2026,10,1),date(2026,11,1))
            with server.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT status,due FROM bookings').fetchone(),('cancelled_calendar',6870))
                self.assertEqual(db.execute('SELECT start,end FROM calendar_cancellations').fetchone(),('2026-10-06T14:00:00','2026-10-06T16:00:00'))
            with patch.object(apple,'calendar_connection',side_effect=AssertionError('must not recreate cancelled booking')):
                apple.export_pending(server.connect)
    def test_all_day_and_transparent_cancelled(self):
        lower=datetime(2026,10,1);upper=datetime(2026,11,1)
        events=[resource(date(2026,10,4),date(2026,10,5)),
                resource(date(2026,10,6),transp='TRANSPARENT'),
                resource(date(2026,10,7),status='CANCELLED')]
        self.assertEqual(apple.intervals(events,lower,upper),
                         [('2026-10-04T00:00:00','2026-10-05T00:00:00')])

    def test_paris_dst_export_roundtrip(self):
        for day in ('2026-07-10','2026-12-10'):
            content=Calendar.from_ical(apple.event_data('a'*32,day+'T17:00:00',day+'T19:00:00'))
            spans=apple.intervals([SimpleNamespace(icalendar_instance=content)],datetime(2026,1,1),datetime(2027,1,1))
            self.assertEqual(spans,[(day+'T17:00:00',day+'T19:00:00')])

    def test_unknown_recurrence_refuses_false_availability(self):
        with self.assertRaises(apple.CalendarUnavailable):
            apple.intervals([resource(date(2026,10,4),rrule={'FREQ':'WEEKLY'})],datetime(2026,10,1),datetime(2026,11,1))

    def test_selected_calendar_only(self):
        class Client:
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def principal(self):return self
            def calendars(self):return [SimpleNamespace(get_display_name=lambda:'Perso'),
                SimpleNamespace(get_display_name=lambda:'EventsBooth360',url='https://p01-caldav.icloud.com/example/')]
        with patch.object(payments,'config',return_value={'ICLOUD_USERNAME':'test','ICLOUD_APP_PASSWORD':'placeholder'}),patch('caldav.DAVClient',Client):
            with apple.calendar_connection() as cal:self.assertEqual(cal.get_display_name(),'EventsBooth360')

    def test_busy_cache_fresh_checkout_and_failure(self):
        from contextlib import contextmanager
        calls=[]
        @contextmanager
        def connection():
            calls.append(1)
            yield SimpleNamespace(search=lambda **kw:[])
        cfg={'ICLOUD_SYNC_ENABLED':'true','ICLOUD_USERNAME':'test','ICLOUD_APP_PASSWORD':'placeholder'}
        apple._cache.clear()
        with patch.object(payments,'config',return_value=cfg),patch.object(apple,'calendar_connection',connection):
            apple.busy(date(2026,10,1),date(2026,11,1));apple.busy(date(2026,10,1),date(2026,11,1))
            self.assertEqual(len(calls),1)
            apple.busy(date(2026,10,1),date(2026,11,1),fresh=True)
            self.assertEqual(len(calls),2)
        with patch.object(payments,'config',return_value=cfg),patch.object(apple,'calendar_connection',side_effect=apple.CalendarUnavailable('offline')):
            with self.assertRaises(apple.CalendarUnavailable):apple.busy(date(2026,10,1),date(2026,11,1),fresh=True)

    def test_export_queue_retry_without_duplicate_and_no_unpaid(self):
        from contextlib import contextmanager
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'DATABASE',Path(folder)/'test.db'),patch.object(payments,'config',return_value={'ICLOUD_SYNC_ENABLED':'true'}):
            with server.connect() as db:
                payments.schema(db)
                slot=db.execute('INSERT INTO slots(start,end) VALUES(?,?)',('2026-10-04T17:00:00','2026-10-04T19:00:00')).lastrowid
                db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',('a'*32,slot,22900,6870,'confirmed','cs_mock','private address',1))
                db.execute('INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)',('b'*32,slot,22900,6870,'pending','cs_mock2','private address',2))
            saved={};calls=[]
            class Remote:
                url='https://example.icloud.com/calendar/'
                def event_by_url(self,url):
                    from urllib.parse import unquote
                    uid=unquote(url.rsplit('/',1)[1][:-4])
                    if uid not in saved:raise NotFoundError()
                    return saved[uid]
                def add_event(self,data):
                    uid=str(Calendar.from_ical(data).walk('VEVENT')[0]['uid']);saved[uid]=data;calls.append(1)
                    # Simulate response loss AFTER remote creation, BEFORE DB acknowledgement.
                    raise apple.CalendarUnavailable('response lost')
            @contextmanager
            def connection():yield Remote()
            with patch.object(apple,'calendar_connection',connection):
                with self.assertRaises(apple.CalendarUnavailable):apple.export_pending(server.connect)
                apple.export_pending(server.connect);apple.export_pending(server.connect)
            self.assertEqual(len(calls),1)
            self.assertNotIn('private address',next(iter(saved.values())))
            with server.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM calendar_exports WHERE exported=1').fetchone()[0],1)
                self.assertEqual(len(server.openings(db,date(2026,10,5),2,extra=[('2026-10-05T00:00:00','2026-10-06T00:00:00')])),0)

if __name__=='__main__':unittest.main()
