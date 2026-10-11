import unittest,tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
import payments,server

class BookingGapTest(unittest.TestCase):
    def test_two_hours_for_site_and_apple_across_midnight(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'DATABASE',Path(folder)/'gap.db'):
            for source in ('site','apple'):
                with self.subTest(source=source),server.connect() as db:
                    db.execute('DELETE FROM slots')
                    periods=[('2026-12-20T20:00:00','2026-12-20T22:00:00')]
                    if source=='site':db.execute('INSERT INTO slots(start,end) VALUES(?,?)',periods[0]);extra=[]
                    else:extra=periods
                    available=server.openings(db,date(2026,12,20),2,extra=extra)
                    self.assertIn('16:00',available)
                    self.assertNotIn('16:30',available)
                    self.assertNotIn('22:30',available)
                    self.assertNotIn('23:00',available)
                    if source=='site':
                        db.execute('DELETE FROM slots')
                        db.execute("INSERT INTO slots(start,end) VALUES('2026-12-19T23:00:00','2026-12-20T09:00:00')")
                        extra=[]
                    else:extra=[('2026-12-19T23:00:00','2026-12-20T09:00:00')]
                    available=server.openings(db,date(2026,12,20),2,extra=extra)
                    self.assertNotIn('10:30',available)
                    self.assertIn('11:00',available)
