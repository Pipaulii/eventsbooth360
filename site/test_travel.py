import unittest
from unittest.mock import patch
from decimal import Decimal
import payments,travel

class TravelTest(unittest.TestCase):
    def setUp(self):
        travel._cache.clear()
        self.config=patch.object(payments,'config',return_value={'TRAVEL_QUOTE_SECRET':'unit-test-secret'})
        self.config.start()
    def tearDown(self):self.config.stop()
    def feature(self,code='13001',postcode='13400'):
        return {'features':[{'properties':{'type':'housenumber','score':.9,'citycode':code,'postcode':postcode,'label':'10 rue Exemple '+postcode},'geometry':{'coordinates':[5.5,43.3]}}]}
    def test_marseille_included_no_route(self):
        with patch.object(travel,'fetch',return_value=self.feature('13213','13013')) as fetch:
            q=travel.quote('134 rue Alphonse Daudet 13013 Marseille')
        self.assertEqual(q['fee_cents'],0);self.assertEqual(fetch.call_count,1)
    def test_round_trip_asymmetric_routes_and_signed_quote(self):
        address='10 rue Exemple 13400 Aubagne'
        with patch.object(travel,'fetch',side_effect=[self.feature(),{'distance':30000},{'distance':32000}]) as fetch:
            q=travel.quote(address);again=travel.quote(address)
        self.assertEqual(q['fee_cents'],806);self.assertEqual(q['round_trip_km'],62)
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(travel.verify(q['token'],address)['fee_cents'],806)
        for token,other in [(q['token']+'x',address),(q['token'],'20 rue Exemple 13400 Aubagne'),(None,address)]:
            with self.assertRaises(travel.TravelError):travel.verify(token,other)
    def test_unavailable_or_imprecise_never_free(self):
        with patch.object(travel,'fetch',side_effect=TimeoutError()):
            with self.assertRaises(travel.TravelError):travel.quote('10 rue Exemple 13400 Aubagne')
        feature=self.feature();feature['features'][0]['properties']['type']='street'
        with patch.object(travel,'fetch',return_value=feature):
            with self.assertRaises(travel.TravelError):travel.quote('10 rue Exemple 13400 Aubagne')
    def test_mismatched_postcode_rejected(self):
        with patch.object(travel,'fetch',return_value=self.feature('13213','13013')):
            with self.assertRaises(travel.TravelError):travel.quote('10 rue Exemple 13400 Aubagne')

    def test_checkout_charges_signed_total_and_rejects_tampering(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace as NS
        from datetime import datetime,timedelta
        import server,apple_calendar
        config={'TRAVEL_QUOTE_SECRET':'unit-test-secret','STRIPE_SECRET_KEY':'sk_test_placeholder','STRIPE_WEBHOOK_SECRET':'whsec_placeholder','SITE_URL':'http://localhost'}
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'DATABASE',Path(folder)/'checkout.db'),patch.object(payments,'config',return_value=config):
            address='10 rue Exemple 13400 Aubagne'
            data=dict(duration=2,payment_mode='deposit',date=(datetime.now()+timedelta(days=3)).strftime('%Y-%m-%d'),time='18:00',address=address,first_name='Camille',last_name='Exemple',phone='0600000000',event_type='Autre',video_name='')
            data['travel_token']=travel.serializer().dumps({'address_hash':travel.digest(address),'fee_cents':806,'round_trip_km':62,'label':address})
            data['fee_cents']=0
            with server.connect() as db:db.execute("INSERT INTO settings VALUES('ready','true')")
            with patch.object(apple_calendar,'busy',return_value=[]),patch.object(apple_calendar,'enabled',return_value=False),patch.object(payments,'client') as client:
                client.return_value=NS(v1=NS(checkout=NS(sessions=NS(create=lambda params,options: self.check_amount(params)))))
                payments.checkout(data,server.connect,server.openings,server.setting)
                data['travel_token']+='x'
                with self.assertRaises(payments.PaymentError):payments.checkout(data,server.connect,server.openings,server.setting)
            with server.connect() as db:
                self.assertEqual(db.execute('SELECT total,due FROM bookings').fetchone(),(23706,7112))
                self.assertEqual(db.execute('SELECT fee FROM booking_travel').fetchone()[0],806)
    def check_amount(self,params):
        from types import SimpleNamespace as NS
        self.assertEqual(params['line_items'][0]['price_data']['unit_amount'],7112)
        return NS(id='cs_unit_test',url='https://checkout.stripe.com/test')
