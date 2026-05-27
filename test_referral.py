#!/usr/bin/env python3
"""
End-to-end test for the /subscribe endpoint.

Hits the running server with sample payloads (direct signup + referral)
so you can verify both Mailchimp flows without filling out the form.

Usage:
  # Default: both flows with placeholder emails
  python3 test_referral.py

  # Use real emails you control (so journey emails actually land in your inbox)
  python3 test_referral.py \
      --direct-email   you@gmail.com \
      --referrer-email you@gmail.com \
      --friend-email   friend@gmail.com

  # Only the referral flow
  python3 test_referral.py --mode referral \
      --referrer-email you@gmail.com --friend-email friend@gmail.com

  # Point at a different server
  python3 test_referral.py --base-url http://localhost:9000
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

EVENT_ID   = '240895'
EVENT_NAME = '* API NUMBERED EVENT'
GRADA_ID   = '224515'
GRADA_NAME = 'Sector 316'


def post_subscribe(base_url, payload):
    req = urllib.request.Request(
        f'{base_url.rstrip("/")}/subscribe',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {'raw': body.decode('utf-8', 'replace')}
    except urllib.error.URLError as e:
        return None, {'error': str(e), 'hint': 'Is server.py running on this port?'}


def verdict(label, code, body, expectations):
    ok = code == 200 and body.get('ok') is True
    mark = '✓' if ok else '✗'
    print(f'  {mark} HTTP {code}  → {body}')
    if ok and expectations:
        print('    In Mailchimp you should now see:')
        for item in expectations:
            print(f'      · {item}')
    return ok


def test_direct(base_url, email):
    print(f'\n▶ DIRECT SIGNUP   email={email}')
    payload = {
        'nombre':     'Test',
        'apellidos':  'Direct',
        'email':      email,
        'event_id':   EVENT_ID,
        'event_name': EVENT_NAME,
        'grada_id':   GRADA_ID,
        'grada_name': GRADA_NAME,
    }
    code, body = post_subscribe(base_url, payload)
    return verdict('direct', code, body, expectations=[
        f'new subscriber: {email}',
        f'tags: event:{EVENT_ID}, grada:{GRADA_ID}, source:avisame',
        'NO REF_BY merge field',
        'NO journey emails (neither A nor B should fire)',
    ])


def test_referral(base_url, referrer, friend):
    print(f'\n▶ REFERRAL SIGNUP  referrer={referrer}  friend={friend}')
    payload = {
        'nombre':         'Test',
        'apellidos':      'Friend',
        'email':          friend,
        'event_id':       EVENT_ID,
        'event_name':     EVENT_NAME,
        'grada_id':       GRADA_ID,
        'grada_name':     GRADA_NAME,
        'referrer_email': referrer,
    }
    code, body = post_subscribe(base_url, payload)
    return verdict('referral', code, body, expectations=[
        f'friend profile: {friend} — tags: source:referral, event:{EVENT_ID}, grada:{GRADA_ID}',
        f'friend profile: REF_BY = {referrer}',
        f'friend profile: note "Referido por {referrer}"',
        f'referrer profile: {referrer} — tagged "referrer:active"  (only if already in audience)',
        'Journey A fires → email to referrer (if in audience)',
        'Journey B fires → email to friend referencing referrer',
    ])


def test_bad_payload(base_url):
    print('\n▶ BAD PAYLOAD     (missing required fields — should reject)')
    code, body = post_subscribe(base_url, {'email': 'incomplete@example.com'})
    ok = code == 400 and not body.get('ok')
    mark = '✓' if ok else '✗'
    print(f'  {mark} HTTP {code}  → {body}   (expected HTTP 400)')
    return ok


def main():
    p = argparse.ArgumentParser(description='Test the /subscribe endpoint end-to-end.')
    p.add_argument('--base-url',       default='http://localhost:8080')
    p.add_argument('--mode',           choices=['direct', 'referral', 'bad', 'all'], default='all')
    p.add_argument('--direct-email',   default='direct-test@example.com')
    p.add_argument('--referrer-email', default='referrer-test@example.com')
    p.add_argument('--friend-email',   default='friend-test@example.com')
    args = p.parse_args()

    print(f'POSTing to {args.base_url}/subscribe')
    results = []

    if args.mode in ('direct', 'all'):
        results.append(('direct',   test_direct(args.base_url, args.direct_email)))
    if args.mode in ('referral', 'all'):
        results.append(('referral', test_referral(args.base_url, args.referrer_email, args.friend_email)))
    if args.mode in ('bad', 'all'):
        results.append(('bad payload', test_bad_payload(args.base_url)))

    print('\n──────── SUMMARY ────────')
    for name, ok in results:
        print(f'  {"✓" if ok else "✗"}  {name}')

    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == '__main__':
    main()
