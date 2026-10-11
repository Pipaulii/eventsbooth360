"""Server-priced road travel quotes; no customer-supplied prices are trusted."""
import hashlib,json,re,time,threading
from decimal import Decimal,ROUND_HALF_UP
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from itsdangerous import URLSafeTimedSerializer,BadSignature,SignatureExpired
import payments
ORIGIN=(5.411268,43.321545)
_cache={};_lock=threading.Lock();_last=0
class TravelError(Exception):pass

def serializer():
    c=payments.config();key=c.get('TRAVEL_QUOTE_SECRET') or c.get('ADMIN_PASSWORD')
    if not key:raise TravelError('Calcul des déplacements temporairement indisponible.')
    return URLSafeTimedSerializer(key,salt='eventsbooth-travel-v1')

def digest(address):return hashlib.sha256(address.strip().casefold().encode()).hexdigest()

def fetch(path,params):
    global _last
    # Stay below IGN routing quota, including concurrent requests in one worker.
    with _lock:
        pause=.25-(time.monotonic()-_last)
        if pause>0:time.sleep(pause)
        _last=time.monotonic()
        req=Request('https://data.geopf.fr/'+path+'?'+urlencode(params),headers={'User-Agent':'EventsBooth360/1.0','Accept':'application/json'})
        with urlopen(req,timeout=8) as response:return json.load(response)

def quote(address):
    if not isinstance(address,str) or not 10<=len(address.strip())<=500 or not re.search(r'\b\d{5}\b',address):raise TravelError('Indiquez le numéro, la rue, le code postal et la ville.')
    address=address.strip();key=digest(address)
    with _lock:cached=_cache.get(key)
    if cached and cached[0]>time.monotonic():data=cached[1]
    else:
        try:
            features=fetch('geocodage/search',{'q':address,'limit':1,'index':'address'})['features']
            if not features:raise TravelError('Adresse introuvable. Vérifiez-la avant de payer.')
            feature=features[0];p=feature['properties'];point=feature['geometry']['coordinates']
            if p.get('type')!='housenumber' or p.get('score',0)<.65 or p.get('postcode') not in re.findall(r'\b\d{5}\b',address):raise TravelError('Adresse imprécise. Indiquez le numéro, la rue, le code postal et la ville.')
            local=p.get('citycode')=='13055' or str(p.get('citycode','')) in {str(13200+n) for n in range(1,17)}
            meters=0
            if not local:
                for a,b in [(ORIGIN,point),(point,ORIGIN)]:
                    route=fetch('navigation/itineraire',{'resource':'bdtopo-osrm','start':','.join(map(str,a)),'end':','.join(map(str,b)),'profile':'car','optimization':'fastest','distanceUnit':'meter','getSteps':'false','getBbox':'false'})
                    distance=Decimal(str(route['distance']))
                    if not distance.is_finite() or distance<=0:raise ValueError()
                    meters+=distance
            fee=int((Decimal(meters)*Decimal('0.013')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            data={'address_hash':key,'label':p['label'],'fee_cents':fee,'round_trip_km':float((Decimal(meters)/1000).quantize(Decimal('.1'))),'included':local,'rate_cents_per_km':13}
            with _lock:
                if len(_cache)>500:_cache.clear()
                _cache[key]=(time.monotonic()+1800,data)
        except TravelError:raise
        except Exception:raise TravelError('Calcul du déplacement indisponible. Réessayez ou contactez-nous avant de payer.') from None
    return {**data,'token':serializer().dumps(data)}

def verify(token,address):
    try:
        data=serializer().loads(token,max_age=3600)
        if data['address_hash']!=digest(address):raise ValueError()
        return data
    except (BadSignature,SignatureExpired,ValueError,TypeError,KeyError):raise TravelError('Recalculez et confirmez les frais de déplacement avant de payer.') from None
