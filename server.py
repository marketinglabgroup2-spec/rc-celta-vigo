#!/usr/bin/env python3
import http.server
import socketserver
import os
import json
import time
import urllib.request
import urllib.parse
import base64
import hashlib
from urllib.error import HTTPError

PORT = 8080
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


def load_env():
    env = {}
    with open(os.path.join(DIRECTORY, '.env')) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip()
    return env

ENV         = load_env()
MC_API_KEY  = ENV.get('MAILCHIMP_API_KEY', '')
MC_AUDIENCE = ENV.get('MAILCHIMP_AUDIENCE_ID', '')
MC_DC       = ENV.get('MAILCHIMP_DC', 'us1')
MC_BASE     = f'https://{MC_DC}.api.mailchimp.com/3.0'

OB_BASE_URL      = ENV.get('ONEBOX_BASE_URL', '').rstrip('/')
OB_CHANNEL_ID    = ENV.get('ONEBOX_CHANNEL_ID', '')
OB_CLIENT_ID     = ENV.get('ONEBOX_CLIENT_ID', '')
OB_CLIENT_SECRET = ENV.get('ONEBOX_CLIENT_SECRET', '')

# Auth token + per-session availability caches (in-process, single-worker only)
_ob_token   = {'value': None, 'expires_at': 0}
_avail_cache = {}  # session_id → {'data': ..., 'expires_at': ...}
AVAIL_TTL = 5      # seconds; balance between freshness and Onebox load


def _onebox_token():
    """Return a cached Onebox access token, re-authing when expired."""
    now = time.time()
    if _ob_token['value'] and now < _ob_token['expires_at'] - 60:
        return _ob_token['value']
    data = urllib.parse.urlencode({
        'grant_type':    'client_credentials',
        'channel_id':    OB_CHANNEL_ID,
        'client_id':     OB_CLIENT_ID,
        'client_secret': OB_CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(
        f'{OB_BASE_URL}/oauth/token',
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    _ob_token['value']      = body['access_token']
    _ob_token['expires_at'] = now + int(body.get('expires_in', 3600))
    return _ob_token['value']


def _ob_get(path):
    """Authenticated GET against Onebox catalog API."""
    token = _onebox_token()
    req = urllib.request.Request(
        f'{OB_BASE_URL}{path}',
        headers={'Authorization': f'Bearer {token}'},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _onebox_availability(session_id):
    """Slim availability summary (used by legacy /availability route)."""
    body = _ob_get(f'/catalog-api/v1/sessions/{session_id}/availability')
    avail = body.get('availability', {}) or {}
    total     = int(avail.get('total') or 0)
    available = int(avail.get('available') or 0)
    return {
        'ok':        True,
        'session':   session_id,
        'total':     total,
        'available': available,
        'percent':   round(100 * available / total) if total else 0,
    }


def _onebox_session(session_id):
    """
    Comprehensive session view: banner data + sectors with per-tier prices.
    Cached for AVAIL_TTL seconds — shared by all concurrent visitors.
    """
    now = time.time()
    cached = _avail_cache.get(session_id)
    if cached and now < cached['expires_at']:
        return cached['data']

    session = _ob_get(f'/catalog-api/v1/sessions/{session_id}')
    avail   = _ob_get(f'/catalog-api/v1/sessions/{session_id}/availability')
    prices  = _ob_get(f'/catalog-api/v1/sessions/{session_id}/prices')

    # Pull the parent event for the richer localized title
    event_id = (session.get('event') or {}).get('id')
    event_full = {}
    if event_id:
        try:
            event_full = _ob_get(f'/catalog-api/v1/events/{event_id}')
        except Exception:
            pass

    def _is_placeholder(s):
        if not s: return True
        x = s.strip()
        return (x.startswith('*')
                or 'API' in x.upper()
                or x.upper() in ('NUMERADO', 'EVENT', 'TEST', 'DEMO'))

    event_texts  = event_full.get('texts') or {}
    title_localized = (event_texts.get('title') or {})
    candidates = [
        title_localized.get('es-ES'),
        title_localized.get('en-US'),
        event_full.get('name'),
        (session.get('event') or {}).get('name'),
        session.get('name'),
    ]
    real_title = next((c for c in candidates if c and not _is_placeholder(c)), None)

    # Tier-name → total price map (default rate)
    rates = prices.get('rates') or []
    default_rate = next((r for r in rates if r.get('default')), rates[0] if rates else {})
    tier_price = {
        pt['name']: pt.get('price', {}).get('total')
        for pt in (default_rate.get('price_types') or [])
    }

    # Sector list with price + per-sector availability
    sectors_out = []
    for s in (avail.get('sectors') or []):
        pt = (s.get('price_types') or [{}])[0]
        pt_avail = pt.get('availability') or {}
        sectors_out.append({
            'id':        s.get('id'),
            'name':      s.get('name'),
            'tier':      pt.get('name'),
            'price':     tier_price.get(pt.get('name')),
            'total':     int(pt_avail.get('total') or 0),
            'available': int(pt_avail.get('available') or 0),
        })

    av_sum = avail.get('availability') or {}
    total     = int(av_sum.get('total') or 0)
    available = int(av_sum.get('available') or 0)

    event = session.get('event') or {}
    venue = session.get('venue') or {}
    p_min = (session.get('price') or {}).get('min') or {}
    p_max = (session.get('price') or {}).get('max') or {}
    subtitle = ((event.get('texts') or {}).get('subtitle') or {})

    out = {
        'ok':         True,
        'session_id': int(session_id),
        'name':       event.get('name') or session.get('name'),
        'real_title': real_title,                            # None when API only has placeholder data
        'subtitle':   subtitle.get('es-ES') or subtitle.get('en-US'),
        'date':       (session.get('date') or {}).get('start'),
        'venue':      venue.get('name'),
        'venue_city': (venue.get('location') or {}).get('city'),
        'on_sale':    bool(session.get('on_sale')),
        'price':      {'min': p_min.get('value'), 'max': p_max.get('value')},
        'availability': {
            'total':     total,
            'available': available,
            'percent':   round(100 * available / total) if total else 0,
        },
        'sectors': sectors_out,
    }
    _avail_cache[session_id] = {'data': out, 'expires_at': now + AVAIL_TTL}
    return out


def _mc_request(method, path, payload=None):
    credentials = base64.b64encode(f'anystring:{MC_API_KEY}'.encode()).decode()
    req = urllib.request.Request(
        f'{MC_BASE}{path}',
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type':  'application/json',
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}


def subscribe(nombre, apellidos, email, event_id, event_name, grada_id, grada_name, referrer_email=None):
    # 1. Upsert member (subscribed status, all merge fields)
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()

    merge_fields = {
        'FNAME':      nombre,
        'LNAME':      apellidos,
        'EVENT_ID':   str(event_id),
        'EVENT_NAME': event_name,
        'GRADA_ID':   str(grada_id),
        'GRADA_NAME': grada_name,
        'GDPR':       'yes',
    }
    if referrer_email:
        merge_fields['REF_BY'] = referrer_email   # populated → triggers merge-field journey

    status, body = _mc_request('PUT',
        f'/lists/{MC_AUDIENCE}/members/{subscriber_hash}',
        {
            'email_address': email,
            'status_if_new': 'subscribed',
            'merge_fields': merge_fields,
        }
    )

    if status not in (200, 201):
        detail = body.get('detail') or body.get('title') or 'Mailchimp error'
        return False, detail

    # 2. Apply segmentation tags — distinguish direct signups from referrals
    source_tag = 'source:referral' if referrer_email else 'source:avisame'
    tags = [
        {'name': f'event:{event_id}',  'status': 'active'},
        {'name': f'grada:{grada_id}',  'status': 'active'},
        {'name': source_tag,           'status': 'active'},
    ]
    _mc_request('POST',
        f'/lists/{MC_AUDIENCE}/members/{subscriber_hash}/tags',
        {'tags': tags}
    )

    # 3. If this was a referral, record who referred them (as a note + tag the referrer)
    if referrer_email:
        _mc_request('POST',
            f'/lists/{MC_AUDIENCE}/members/{subscriber_hash}/notes',
            {'note': f'Referido por {referrer_email}'}
        )

        # Tag the referrer (if they exist in the list) as an active referrer
        referrer_hash = hashlib.md5(referrer_email.lower().encode()).hexdigest()
        _mc_request('POST',
            f'/lists/{MC_AUDIENCE}/members/{referrer_hash}/tags',
            {'tags': [{'name': 'referrer:active', 'status': 'active'}]}
        )

    return True, body.get('status', 'subscribed')


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, fmt, *args):
        print(f'  {self.address_string()} — {fmt % args}')

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        session_id = (qs.get('session') or ['240895'])[0].strip()

        # /session?session=240895 — full live data for banner + sectors
        if parsed.path == '/session':
            try:
                self._json(200, _onebox_session(session_id))
            except HTTPError as e:
                self._json(e.code, {'ok': False, 'error': f'Onebox HTTP {e.code}'})
            except Exception as e:
                self._json(500, {'ok': False, 'error': str(e)})
            return

        # /availability?session=240895 — slim count summary
        if parsed.path == '/availability':
            try:
                self._json(200, _onebox_availability(session_id))
            except HTTPError as e:
                self._json(e.code, {'ok': False, 'error': f'Onebox HTTP {e.code}'})
            except Exception as e:
                self._json(500, {'ok': False, 'error': str(e)})
            return

        return super().do_GET()

    def do_POST(self):
        if self.path != '/subscribe':
            self.send_error(404)
            return

        length = int(self.headers.get('Content-Length', 0))
        raw    = self.rfile.read(length)
        ct     = self.headers.get('Content-Type', '')
        data   = json.loads(raw) if 'application/json' in ct \
                 else dict(urllib.parse.parse_qsl(raw.decode('utf-8')))

        nombre         = data.get('nombre',         '').strip()
        apellidos      = data.get('apellidos',      '').strip()
        email          = data.get('email',          '').strip()
        event_id       = data.get('event_id',       '').strip()
        event_name     = data.get('event_name',     '').strip()
        grada_id       = data.get('grada_id',       '').strip()
        grada_name     = data.get('grada_name',     '').strip()
        referrer_email = data.get('referrer_email', '').strip() or None

        if not all([nombre, apellidos, email, grada_id]):
            self._json(400, {'ok': False, 'error': 'Faltan campos obligatorios.'})
            return

        ok, result = subscribe(nombre, apellidos, email,
                               event_id, event_name, grada_id, grada_name,
                               referrer_email=referrer_email)

        if ok:
            ref_note = f' | referred by {referrer_email}' if referrer_email else ''
            print(f'  ✓  {email} | event:{event_id} | grada:{grada_id} | tags applied{ref_note}')
            msg = ('¡Listo! Avisaremos a tu amigo en cuanto salgan las entradas.'
                   if referrer_email
                   else '¡Te avisaremos en cuanto salgan las entradas!')
            self._json(200, {'ok': True, 'message': msg})
        else:
            print(f'  ✗  {email} — {result}')
            self._json(500, {'ok': False, 'error': result})

    def _json(self, code, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',  'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


print(f'Serving RC Celta ¡Avísame! at http://localhost:{PORT}/landing.html')
print('Press Ctrl+C to stop.\n')

with socketserver.TCPServer(('', PORT), Handler) as httpd:
    httpd.allow_reuse_address = True
    httpd.serve_forever()
