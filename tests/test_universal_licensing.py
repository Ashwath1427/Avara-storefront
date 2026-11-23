"""Universal-installer licensing: codes, one-time use, expiry, signed tickets, owner security.

Uses a disposable SQLite store and a throwaway Ed25519 key. Never contacts Render,
Supabase or email providers.
"""
import base64
import json
import os
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Response
from fastapi.testclient import TestClient

from storefront import licensing, server

OWNER_KEY = 'owner-key-for-universal-tests-0123456789'


def new_key_pair():
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return base64.urlsafe_b64encode(seed).rstrip(b'=').decode(), licensing.public_key_text(key)


def token(n: int) -> str:
    return hashlib.sha256(f'test-installation-{n}'.encode()).hexdigest()


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private, self.public = new_key_pair()
        patches = [
            patch.object(server, 'DATABASE_URL', ''),
            patch.object(server, 'DB_PATH', str(Path(self.temp.name) / 'store.db')),
            patch.dict('os.environ', {'AVARA_STORE_ADMIN_KEY': OWNER_KEY, 'AVARA_TICKET_SIGNING_KEY': self.private,
                                      'AVARA_CODE_PEPPER': 'test-pepper', 'AVARA_STORE_SECURE_COOKIE': '1',
                                      'AVARA_PUBLIC_DOWNLOADS': 'false', 'RENDER': ''}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(server.app, base_url='https://testserver')
        self.addCleanup(self.client.close)

    # -- helpers
    def account(self, n=1, password='a-long-password-1'):
        """Give installation n a signed-in AVARA account (orders require one)."""
        now = int(time.time())
        with server.db() as con:
            row = con.execute('SELECT id FROM users WHERE email=?', (f'user{n}@example.test',)).fetchone()
            if not row:
                salt = b'0123456789abcdef'
                row = con.execute('INSERT INTO users(name,email,salt,password) VALUES (?,?,?,?) RETURNING id',
                                  (f'User {n}', f'user{n}@example.test', salt, server._hash_password(password, salt))).fetchone()
            server.upsert_installation(con, token(n), 'windows', '1.1.0', now)
            con.execute('UPDATE app_installations SET user_id=? WHERE id=?', (row[0], licensing.installation_id(token(n))))
        return row[0]

    def register(self, n=1, plan='basic', platform='windows'):
        self.account(n)
        response = self.client.post('/api/v2/installations/register',
                                    json={'token': token(n), 'plan_id': plan, 'platform': platform, 'app_version': '1.1.0'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def status(self, n=1):
        response = self.client.post('/api/v2/activation/status', json={'token': token(n), 'platform': 'windows'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return licensing.verify_ticket(body['ticket'], [self.public]), body['code']

    def login(self, client=None):
        client = client or self.client
        response = client.post('/api/owner/login', json={'key': OWNER_KEY})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['csrf']

    def owner_post(self, path, body=None, csrf=None):
        return self.client.post(path, json=body or {}, headers={'X-CSRF-Token': csrf} if csrf else {})

    def audit_actions(self):
        with server.db() as con:
            return [row[0] for row in con.execute('SELECT action FROM audit_log ORDER BY id')]


class CodeTests(unittest.TestCase):
    def test_codes_are_short_unambiguous_and_installation_bound(self):
        seen = set()
        for n in range(300):
            code = licensing.derive_code(token(n), licensing.new_nonce())
            self.assertRegex(code, r'^AVR-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}$')
            self.assertFalse(set(code[4:]) & set('01OILU'))
            seen.add(code)
        self.assertEqual(len(seen), 300)
        nonce = licensing.new_nonce()
        self.assertEqual(licensing.derive_code(token(1), nonce), licensing.derive_code(token(1), nonce))
        self.assertNotEqual(licensing.derive_code(token(1), nonce), licensing.derive_code(token(2), nonce))

    def test_normalize_accepts_whatsapp_variants_only(self):
        code = licensing.derive_code(token(9), b'n' * 16)
        body = code[4:].replace('-', '')
        for variant in (code, code.lower(), ' ' + code + ' ', body, body.lower(), 'avr ' + body[:4] + ' ' + body[4:]):
            self.assertEqual(licensing.normalize_code(variant), code)
        for bad in ('AVR-0000-0000', 'AVR-IIII-LLLL', 'AVR-ABCD', '', 'x' * 60, None, 'AVR-ABCD-EFGH-JKMN'):
            self.assertIsNone(licensing.normalize_code(bad))
        with patch.dict('os.environ', {'AVARA_CODE_PEPPER': 'pepper'}):
            self.assertEqual(licensing.code_hash(code), licensing.code_hash(code.lower()))
            self.assertNotIn(body, licensing.code_hash(code))
        self.assertTrue(licensing.code_hint(code).endswith(code[-4:]))


class TicketTests(unittest.TestCase):
    def test_sign_verify_and_reject_tampering(self):
        private, public = new_key_pair()
        key = licensing.load_private_key(private)
        payload = {'installation_id': 'ins_x', 'plan': 'basic', 'expires': 2_000_000_000, 'six_month': False, 'issued_at': 1}
        ticket = licensing.sign_ticket(payload, key)
        self.assertEqual(licensing.verify_ticket(ticket, [public])['plan'], 'basic')
        # Change the plan inside the payload: signature no longer matches.
        forged_body = licensing.canonical_json(dict(payload, plan='pro', v=1))
        forged = dict(ticket, payload=base64.urlsafe_b64encode(forged_body).rstrip(b'=').decode())
        with self.assertRaises(ValueError):
            licensing.verify_ticket(forged, [public])
        with self.assertRaises(ValueError):
            licensing.verify_ticket(ticket, [new_key_pair()[1]])
        with self.assertRaises(ValueError):
            licensing.verify_ticket({'payload': ticket['payload'], 'sig': ''}, [public])
        with self.assertRaises(ValueError):
            licensing.verify_ticket(None, [public])
        # Key rotation: any listed key may verify.
        self.assertTrue(licensing.verify_ticket(ticket, [new_key_pair()[1], public]))

    def test_pem_signing_key_is_accepted(self):
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        loaded = licensing.load_private_key(pem.replace('\n', '\\n'))
        self.assertEqual(licensing.public_key_text(loaded), licensing.public_key_text(key))
        with self.assertRaises(ValueError):
            licensing.load_private_key('dG9vLXNob3J0')


class UniversalFlowTests(StoreTestCase):
    def test_plan_catalog_matches_published_prices(self):
        plans = {plan['id']: plan for plan in self.client.get('/api/plans').json()['plans']}
        self.assertEqual({k: (v['price_inr'], v['term_days'], v['tier']) for k, v in plans.items()}, {
            'basic': (150, 30, 'basic'), 'pro': (200, 30, 'pro'),
            'six_month_basic': (1000, 180, 'basic'), 'six_month_pro': (1400, 180, 'pro')})

    def test_choose_plan_code_approve_unlocks_with_signed_ticket(self):
        first = self.register(1, 'basic')
        code = first['code']
        self.assertEqual(code['status'], 'pending')
        self.assertRegex(code['code'], r'^AVR-\w{4}-\w{4}$')
        self.assertEqual((code['plan_id'], code['price_inr']), ('basic', 150))
        self.assertAlmostEqual(code['expires_at'] - time.time(), 48 * 3600, delta=60)
        ticket = licensing.verify_ticket(first['ticket'], [self.public])
        self.assertEqual((ticket['state'], ticket['installation_id']), ('none', licensing.installation_id(token(1))))
        # Same plan again returns the same code (customer may already have sent it).
        self.assertEqual(self.register(1, 'basic')['code']['code'], code['code'])
        # Only hashes and a hint are stored.
        with server.db() as con:
            stored = ' '.join(str(v) for row in con.execute('SELECT * FROM activation_codes') for v in row)
            stored += ' '.join(str(v) for row in con.execute('SELECT * FROM app_installations') for v in row)
        self.assertNotIn(code['code'], stored)
        self.assertNotIn(code['code'][4:].replace('-', ''), stored)
        self.assertNotIn(token(1), stored)

        csrf = self.login()
        listing = self.client.get('/api/owner/license-orders', params={'status': 'pending'}).json()['orders']
        self.assertEqual(len(listing), 1)
        self.assertEqual((listing[0]['plan_name'], listing[0]['price_inr'], listing[0]['status']), ('Basic', 150, 'pending'))
        self.assertTrue(listing[0]['code_hint'].endswith(code['code'][-4:]))
        found = self.client.get('/api/owner/license-orders', params={'q': code['code'].lower()}).json()['orders']
        self.assertEqual([row['id'] for row in found], [listing[0]['id']])
        self.assertEqual((listing[0]['customer_name'], listing[0]['customer_email']), ('User 1', 'user1@example.test'))
        by_email = self.client.get('/api/owner/license-orders', params={'q': 'USER1@example.test'}).json()['orders']
        self.assertEqual([row['id'] for row in by_email], [listing[0]['id']])

        approved = self.owner_post('/api/owner/license-orders/approve', {'code': code['code']}, csrf)
        self.assertEqual(approved.status_code, 200, approved.text)
        ticket, pending = self.status(1)
        self.assertEqual((ticket['state'], ticket['plan'], ticket['plan_id'], ticket['six_month']), ('active', 'basic', 'basic', False))
        self.assertAlmostEqual(ticket['expires'] - time.time(), 30 * 86400, delta=60)
        self.assertLessEqual(ticket['ticket_expires'], ticket['expires'])
        self.assertIsNone(pending)

        # One-time: the same code can never activate again, and the attempt is logged.
        for attempt in (code['code'], code['code'].replace('-', '').lower()):
            reuse = self.owner_post('/api/owner/license-orders/approve', {'code': attempt}, csrf)
            self.assertEqual(reuse.status_code, 409)
            self.assertIn('already used', reuse.json()['detail'])
        with server.db() as con:
            refusals = con.execute("SELECT detail FROM audit_log WHERE action='approve_refused'").fetchall()
        self.assertEqual(len(refusals), 2)
        self.assertTrue(all('code_reuse' in row[0] for row in refusals))
        self.assertIn('license_order_approved', self.audit_actions())

    def test_code_cannot_activate_another_installation(self):
        one = self.register(1, 'pro')['code']['code']
        self.register(2, 'basic')
        csrf = self.login()
        self.assertEqual(self.owner_post('/api/owner/license-orders/approve', {'code': one}, csrf).status_code, 200)
        self.assertEqual(self.status(1)[0]['state'], 'active')
        self.assertEqual(self.status(2)[0]['state'], 'none')
        self.assertEqual(self.status(3)[0]['state'], 'none')

    def test_plan_change_before_approval_issues_new_code_and_kills_old(self):
        old = self.register(1, 'basic')['code']['code']
        new = self.register(1, 'six_month_pro')['code']
        self.assertNotEqual(old, new['code'])
        self.assertEqual((new['plan_id'], new['price_inr']), ('six_month_pro', 1400))
        csrf = self.login()
        stale = self.owner_post('/api/owner/license-orders/approve', {'code': old}, csrf)
        self.assertEqual(stale.status_code, 409)
        self.assertIn('replaced by a newer code', stale.json()['detail'])
        self.assertEqual(self.owner_post('/api/owner/license-orders/approve', {'code': new['code']}, csrf).status_code, 200)
        ticket = self.status(1)[0]
        self.assertEqual((ticket['plan'], ticket['plan_id'], ticket['six_month']), ('pro', 'six_month_pro', True))
        self.assertAlmostEqual(ticket['expires'] - time.time(), 180 * 86400, delta=60)

    def test_unapproved_code_expires_after_48_hours_and_can_be_replaced(self):
        code = self.register(1, 'pro')['code']['code']
        with server.db() as con:
            con.execute('UPDATE activation_codes SET expires_at=?', (int(time.time()) - 1,))
        self.assertEqual(self.status(1)[1]['status'], 'expired')
        self.assertIsNone(self.status(1)[1]['code'])
        csrf = self.login()
        refused = self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf)
        self.assertEqual(refused.status_code, 409)
        self.assertIn('expired', refused.json()['detail'])
        listing = self.client.get('/api/owner/license-orders', params={'status': 'expired'}).json()['orders']
        self.assertEqual(len(listing), 1)
        fresh = self.register(1, 'pro')['code']
        self.assertNotEqual(fresh['code'], code)
        self.assertEqual(fresh['status'], 'pending')

    def test_reject_then_new_code(self):
        self.register(1, 'basic')
        csrf = self.login()
        order_id = self.client.get('/api/owner/license-orders').json()['orders'][0]['id']
        self.assertEqual(self.owner_post(f'/api/owner/license-orders/{order_id}/reject', {'reason': 'no payment'}, csrf).status_code, 200)
        self.assertEqual(self.status(1)[1]['status'], 'rejected')
        self.assertEqual(self.owner_post('/api/owner/license-orders/approve', {'order_id': order_id}, csrf).status_code, 409)
        self.assertEqual(self.register(1, 'basic')['code']['status'], 'pending')

    def test_owner_controls_are_reflected_in_tickets(self):
        code = self.register(1, 'basic')['code']['code']
        csrf = self.login()
        self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf)
        entitlement = self.client.get('/api/owner/entitlements').json()['entitlements'][0]
        path = f"/api/owner/entitlements/{entitlement['id']}"
        expires = self.status(1)[0]['expires']

        self.assertEqual(self.owner_post(path, {'action': 'pause'}, csrf).status_code, 200)
        self.assertEqual(self.status(1)[0]['state'], 'paused')
        with server.db() as con:  # Paused for ten days.
            con.execute('UPDATE entitlements SET paused_at=paused_at-864000')
        self.assertEqual(self.owner_post(path, {'action': 'resume'}, csrf).status_code, 200)
        ticket = self.status(1)[0]
        self.assertEqual(ticket['state'], 'active')
        self.assertAlmostEqual(ticket['expires'] - expires, 864000, delta=5)

        self.assertEqual(self.owner_post(path, {'action': 'change_plan', 'plan_id': 'pro'}, csrf).status_code, 200)
        self.assertEqual(self.status(1)[0]['plan'], 'pro')
        self.assertEqual(self.owner_post(path, {'action': 'change_plan', 'plan_id': 'six_month_basic'}, csrf).status_code, 200)
        ticket = self.status(1)[0]
        self.assertEqual((ticket['plan'], ticket['six_month']), ('basic', True))
        self.assertGreaterEqual(ticket['expires'], time.time() + 180 * 86400 - 60)

        before = ticket['expires']
        self.assertEqual(self.owner_post(path, {'action': 'extend', 'days': 15}, csrf).status_code, 200)
        self.assertEqual(self.status(1)[0]['expires'], before + 15 * 86400)

        self.assertEqual(self.owner_post(path, {'action': 'cancel'}, csrf).status_code, 200)
        self.assertEqual(self.status(1)[0]['state'], 'cancelled')
        # Cancellation is final: nothing on the owner side brings it back.
        for action in ({'action': 'cancel'}, {'action': 'resume'}, {'action': 'extend', 'days': 30},
                       {'action': 'change_plan', 'plan_id': 'pro'}, {'action': 'pause'}):
            refused = self.owner_post(path, action, csrf)
            self.assertEqual(refused.status_code, 409, action)
        self.assertEqual(self.status(1)[0]['state'], 'cancelled')

        # Renewal after cancel: a new code, approved, starts a fresh term.
        renewal = self.register(1, 'pro')['code']['code']
        self.owner_post('/api/owner/license-orders/approve', {'code': renewal}, csrf)
        ticket = self.status(1)[0]
        self.assertEqual((ticket['state'], ticket['plan']), ('active', 'pro'))
        actions = self.audit_actions()
        for action in ('entitlement_pause', 'entitlement_resume', 'entitlement_change_plan', 'entitlement_extend',
                       'entitlement_cancel', 'owner_login'):
            self.assertIn(action, actions)

    def test_renewal_while_active_extends_from_current_expiry(self):
        csrf = self.login()
        self.owner_post('/api/owner/license-orders/approve', {'code': self.register(1, 'basic')['code']['code']}, csrf)
        first = self.status(1)[0]['expires']
        self.owner_post('/api/owner/license-orders/approve', {'code': self.register(1, 'basic')['code']['code']}, csrf)
        self.assertAlmostEqual(self.status(1)[0]['expires'], first + 30 * 86400, delta=5)

    def test_ticket_offline_window_is_capped_by_plan_expiry(self):
        csrf = self.login()
        self.owner_post('/api/owner/license-orders/approve', {'code': self.register(1, 'basic')['code']['code']}, csrf)
        ticket = self.status(1)[0]
        self.assertAlmostEqual(ticket['ticket_expires'] - ticket['issued_at'], 7 * 86400, delta=5)
        with patch.dict('os.environ', {'AVARA_TICKET_OFFLINE_DAYS': '400'}):
            ticket = self.status(1)[0]
        self.assertEqual(ticket['ticket_expires'], ticket['expires'])


class OwnerSecurityTests(StoreTestCase):
    def test_owner_endpoints_require_login_and_csrf(self):
        self.register(1, 'basic')
        anonymous = TestClient(server.app, base_url='https://testserver')
        self.addCleanup(anonymous.close)
        for method, path in (('get', '/api/owner/license-orders'), ('get', '/api/owner/entitlements'),
                             ('get', '/api/owner/audit'), ('post', '/api/owner/license-orders/approve'),
                             ('post', '/api/owner/license-orders/1/reject'), ('post', '/api/owner/entitlements/1'),
                             ('get', '/api/admin/orders'), ('post', '/api/admin/order/1/approve')):
            response = getattr(anonymous, method)(path, **({'json': {'action': 'pause'}} if method == 'post' else {}))
            self.assertEqual(response.status_code, 401, path)
        # Wrong key is refused and logged; the attempt counter is enforced.
        self.assertEqual(self.client.post('/api/owner/login', json={'key': 'wrong-key-wrong-key-wrong'}).status_code, 401)
        self.assertIn('owner_login_failed', self.audit_actions())
        csrf = self.login()
        cookie = self.client.cookies.get(server.OWNER_COOKIE)
        self.assertTrue(cookie)
        set_cookie = self.client.post('/api/owner/login', json={'key': OWNER_KEY}).headers['set-cookie'].lower()
        for flag in ('httponly', 'secure', 'samesite=strict'):
            self.assertIn(flag, set_cookie)
        csrf = self.client.get('/api/owner/session').json()['csrf']
        # Cookie alone (a forged cross-site form) is not enough for a state change.
        no_csrf = self.client.post('/api/owner/license-orders/approve', json={'order_id': 1})
        self.assertEqual(no_csrf.status_code, 403)
        bad_csrf = self.owner_post('/api/owner/license-orders/approve', {'order_id': 1}, 'f' * 64)
        self.assertEqual(bad_csrf.status_code, 403)
        self.assertEqual(self.owner_post('/api/owner/license-orders/approve', {'order_id': 1}, csrf).status_code, 200)
        # Reads work with the cookie; logout ends the session.
        self.assertEqual(self.client.get('/api/owner/audit').status_code, 200)
        self.assertEqual(self.owner_post('/api/owner/logout', {}, csrf).status_code, 200)
        self.client.cookies.set(server.OWNER_COOKIE, cookie, domain='testserver')
        self.assertEqual(self.client.get('/api/owner/license-orders').status_code, 401)
        # Scripts may still use the owner key as a Bearer header.
        bearer = self.client.get('/api/owner/license-orders', headers={'Authorization': 'Bearer ' + OWNER_KEY})
        self.assertEqual(bearer.status_code, 200)
        self.assertEqual(self.client.get('/api/owner/license-orders', headers={'Authorization': 'Bearer nope'}).status_code, 401)

    def test_owner_login_is_rate_limited(self):
        for _ in range(10):
            self.client.post('/api/owner/login', json={'key': 'wrong-key-wrong-key-wrong'})
        self.assertEqual(self.client.post('/api/owner/login', json={'key': OWNER_KEY}).status_code, 429)

    def test_code_creation_and_checks_are_rate_limited(self):
        for plan in ('basic', 'pro') * 4:
            self.register(1, plan)
        limited = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'basic'})
        self.assertEqual(limited.status_code, 429)
        for n in range(10, 22):
            self.client.post('/api/v2/installations/register', json={'token': token(n), 'plan_id': 'basic'})
        self.assertEqual(self.client.post('/api/v2/installations/register',
                                          json={'token': token(99), 'plan_id': 'basic'}).status_code, 429)

    def test_unsigned_service_refuses_v2_and_bad_input(self):
        with patch.dict('os.environ', {'AVARA_TICKET_SIGNING_KEY': ''}):
            self.assertEqual(self.client.post('/api/v2/activation/status', json={'token': token(1)}).status_code, 503)
        self.assertEqual(self.client.post('/api/v2/installations/register', json={'token': 'zz', 'plan_id': 'basic'}).status_code, 422)
        self.assertEqual(self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'gold'}).status_code, 400)

    def test_audit_log_records_every_owner_action(self):
        csrf = self.login()
        code = self.register(1, 'basic')['code']['code']
        self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf)
        entries = self.client.get('/api/owner/audit').json()['entries']
        self.assertEqual(entries[0]['action'], 'license_order_approved')
        self.assertEqual(entries[0]['actor'][:14], 'owner:session:')
        self.assertNotIn(code, str(entries))


class LegacyCompatibilityTests(StoreTestCase):
    """Existing customers and already-installed old copies keep their answers."""

    def seed_live_like_data(self):
        now = int(time.time())
        rows = [  # order id, plan label, order status, platform, deleted, installation state, days left
            (1, 'Pro — ₹200/month', 'approved', 'windows', True, 'cancelled', 29),
            (2, 'Basic — ₹150/month', 'approved', 'windows', True, 'cancelled', 29),
            (3, 'Pro — ₹200/month', 'approved', 'windows', True, None, 0),
            (8, 'Pro — ₹200/month', 'approved', 'mac', True, 'active', 30),
            (9, '6-Month Pro — ₹1,400/6 months', 'approved', 'windows', True, 'active', 180),
            (10, '6-Month Pro — ₹1,400/6 months', 'approved', 'mac', False, 'active', 180),
            (11, 'Basic — ₹150/month', 'approved', 'windows', False, 'paused', 12),
            (12, '6-Month Basic — ₹1,000/6 months', 'approved', 'windows', False, 'active', -2),
        ]
        with server.db() as con:
            con.execute("INSERT INTO users VALUES (1,'Legacy','legacy@example.test',X'00',X'00')")
            server.activation_table(con)
            for order_id, label, status, platform, deleted, state, days in rows:
                con.execute('INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status,platform,deleted_at,approved_at) '
                            "VALUES (?,1,?,'None','draft-1',?,?,?,?,?)",
                            (order_id, label, now - 86400, status, platform, now - 3600 if deleted else None, now - 86400))
                if state:
                    tier = 'pro' if 'Pro' in label else 'basic'
                    con.execute('INSERT INTO installations(code,order_id,plan,expires,state,paused_at) VALUES (?,?,?,?,?,?)',
                                (licensing.legacy_code(token(order_id)), order_id, tier, now + days * 86400, state,
                                 now - 100 if state != 'active' else None))
        return [order_id for order_id, *_ in rows]

    def legacy_answer(self, n):
        return server.check_installation(server.InstallationCheck(token=token(n)), Response())

    def test_migration_preserves_every_old_answer(self):
        orders = self.seed_live_like_data()
        before = {n: self.legacy_answer(n) for n in orders + [77]}
        with server.db() as con:
            copied = server.migrate_legacy_entitlements(con)
            again = server.migrate_legacy_entitlements(con)
            legacy_rows = con.execute('SELECT * FROM installations ORDER BY code').fetchall()
        self.assertEqual((copied, again), (7, 0))
        after = {n: self.legacy_answer(n) for n in orders + [77]}
        self.assertEqual(after, before)
        self.assertEqual(before[10]['status'], 'active')
        self.assertTrue(before[10]['six_month'])
        self.assertEqual(before[8]['status'], 'cancelled')  # Deleted order: still cancelled.
        self.assertEqual(before[11]['status'], 'paused')
        self.assertEqual(before[12]['status'], 'expired')
        self.assertEqual(before[77]['status'], 'pending')
        with server.db() as con:  # Legacy rows were not rewritten.
            self.assertEqual(con.execute('SELECT * FROM installations ORDER BY code').fetchall(), legacy_rows)

    def test_existing_customer_installing_universal_app_keeps_plan(self):
        self.seed_live_like_data()
        with server.db() as con:
            server.migrate_legacy_entitlements(con)
        ticket, code = self.status(10)
        self.assertEqual((ticket['state'], ticket['plan'], ticket['plan_id'], ticket['six_month']),
                         ('active', 'pro', 'six_month_pro', True))
        self.assertEqual(ticket['expires'], self.legacy_answer(10)['expires'])
        self.assertIsNone(code)
        # Owner controls on the migrated plan reach both the new and the old app.
        csrf = self.login()
        entitlement = next(e for e in self.client.get('/api/owner/entitlements').json()['entitlements']
                           if e['legacy_order_id'] == 10)
        self.assertTrue(entitlement['legacy'])
        self.owner_post(f"/api/owner/entitlements/{entitlement['id']}", {'action': 'pause'}, csrf)
        self.assertEqual(self.status(10)[0]['state'], 'paused')
        self.assertEqual(self.legacy_answer(10)['status'], 'paused')
        # Unknown tokens never create rows through the status check.
        self.status(555)
        with server.db() as con:
            self.assertIsNone(con.execute('SELECT 1 FROM app_installations WHERE id=?',
                                          (licensing.installation_id(token(555)),)).fetchone())

    def test_unmigrated_legacy_customer_is_recognized_on_first_launch(self):
        self.seed_live_like_data()  # Legacy rows only; migration never ran.
        ticket = self.status(10)[0]
        self.assertEqual((ticket['state'], ticket['plan_id']), ('active', 'six_month_pro'))
        self.assertEqual(self.status(8)[0]['state'], 'cancelled')  # Deleted order stays cancelled.
        self.assertEqual(self.status(77)[0]['state'], 'none')
        self.assertEqual(self.legacy_answer(10)['status'], 'active')

    def test_legacy_owner_actions_still_reach_old_copies(self):
        owner = SimpleNamespace(headers={'Authorization': 'Bearer ' + OWNER_KEY}, client=None)
        with server.db() as con:
            con.execute("INSERT INTO users VALUES (1,'A','a@example.test',X'00',X'00')")
            con.execute("INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status) VALUES (1,1,'Basic — ₹150/month','None','t',0,'approved')")
        server.activate_installation(1, server.ActivationApproval(code='BASIC-' + licensing.legacy_code(token(5))), owner)
        self.assertEqual(self.legacy_answer(5)['plan'], 'basic')
        server.change_subscription(1, server.SubscriptionChange(action='change_plan', plan='pro'), owner)
        self.assertEqual(self.legacy_answer(5)['plan'], 'pro')
        server.delete_order(1, owner)
        self.assertEqual(self.legacy_answer(5)['status'], 'cancelled')
        server.restore_order(1, owner)
        self.assertEqual(self.legacy_answer(5)['status'], 'active')
        self.assertEqual(self.status(5)[0]['plan'], 'pro')  # Universal app on the same computer.
        actions = self.audit_actions()
        for action in ('legacy_installation_activated', 'legacy_subscription_change_plan', 'legacy_order_deleted',
                       'legacy_order_restored'):
            self.assertIn(action, actions)


class AccountAndDownloadTests(StoreTestCase):
    def test_customer_account_shows_linked_plan_and_expiry(self):
        code = self.register(1, 'pro')['code']['code']
        self.assertEqual(self.client.post('/api/login', json={'email': 'user1@example.test', 'password': 'a-long-password-1'}).status_code, 200)
        self.assertEqual(self.client.post('/api/account/link-installation', json={'code': code}).status_code, 200)
        pending = self.client.get('/api/account/installations').json()
        self.assertEqual(pending['pending'][0]['plan_name'], 'Pro')
        owner = TestClient(server.app, base_url='https://testserver')
        self.addCleanup(owner.close)
        csrf = owner.post('/api/owner/login', json={'key': OWNER_KEY}).json()['csrf']
        owner.post('/api/owner/license-orders/approve', json={'code': code}, headers={'X-CSRF-Token': csrf})
        mine = self.client.get('/api/account/installations').json()['installations']
        self.assertEqual((mine[0]['plan_name'], mine[0]['state']), ('Pro', 'active'))
        self.assertAlmostEqual(mine[0]['expires_at'] - time.time(), 30 * 86400, delta=60)

    def test_old_website_plan_shows_its_device_and_how_to_update(self):
        self.client.post('/api/account', json={'name': 'Old', 'email': 'old@example.test', 'password': 'a-long-password-1'})
        self.client.post('/api/login', json={'email': 'old@example.test', 'password': 'a-long-password-1'})
        now = int(time.time())
        with server.db() as con:
            user = con.execute("SELECT id FROM users WHERE email='old@example.test'").fetchone()[0]
            con.execute("INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status,platform,approved_at) "
                        "VALUES (10,?,'6-Month Pro — ₹1,400/6 months','None','draft-1',?,'approved','mac',?)", (user, now, now))
            server.activation_table(con)
            con.execute("INSERT INTO installations(code,order_id,plan,expires,state) VALUES (?,10,'pro',?,'active')",
                        (licensing.legacy_code(token(10)), now + 150 * 86400))
            server.migrate_legacy_entitlements(con)
        row = self.client.get('/api/account/installations').json()['installations'][0]
        self.assertEqual((row['plan_name'], row['platform'], row['state'], row['needs_new_app']), ('6-Month Pro', 'mac', 'active', True))
        self.status(10)  # The customer opens the new app on that Mac: the plan links to it.
        self.assertFalse(self.client.get('/api/account/installations').json()['installations'][0]['needs_new_app'])

    def test_public_downloads_stay_closed_until_enabled(self):
        status = self.client.get('/api/downloads').json()
        self.assertFalse(status['platforms']['windows']['available'])
        self.assertFalse(status['platforms']['mac']['available'])
        self.assertEqual(self.client.get('/download/windows').status_code, 503)
        self.assertEqual(self.client.get('/download/linux').status_code, 404)

    def test_public_download_streams_verified_parts(self):
        import io
        data = b'universal-installer-bytes' * 1000
        parts = [data[:10000], data[10000:]]
        release = {'enabled': True, 'filename': 'AVARA-1.1.0-windows-setup.exe', 'size': len(data),
                   'sha256': hashlib.sha256(data).hexdigest(),
                   'parts': [{'name': f'p{i}', 'size': len(p), 'sha256': hashlib.sha256(p).hexdigest()} for i, p in enumerate(parts)]}
        manifest = {'enabled': True, 'version': '1.1.0', 'platforms': {'windows': release}}
        opened = iter(io.BytesIO(p) for p in parts)
        with patch.object(server, 'UNIVERSAL_RELEASE', manifest), patch.object(server, 'storage_parts_ready', return_value=True), \
             patch.object(server, 'storage_object_request', side_effect=lambda name, method='GET': name), \
             patch.object(server.urllib.request, 'urlopen', side_effect=lambda request, timeout: next(opened)), \
             patch.dict('os.environ', {'AVARA_PUBLIC_DOWNLOADS': 'true'}):
            self.assertTrue(self.client.get('/api/downloads').json()['platforms']['windows']['available'])
            response = self.client.get('/download/windows')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, data)
        self.assertIn('AVARA-1.1.0-windows-setup.exe', response.headers['content-disposition'])


class AppAccountAndEmailTests(StoreTestCase):
    def sign(self, mode, n=1, email='buyer@example.test', password='a-long-password-1', name='Buyer'):
        return self.client.post(f'/api/v2/account/{mode}', json={'token': token(n), 'email': email, 'password': password,
                                                                  'name': name, 'platform': 'windows'})

    def test_sign_in_says_whether_the_email_has_no_account_or_the_password_is_wrong(self):
        missing = self.sign('login', email='nobody-here@example.test')
        self.assertEqual(missing.status_code, 404)
        self.assertIn('no AVARA account for this email', missing.json()['detail'])
        self.sign('create')
        wrong = self.sign('login', n=2, password='wrong-password-123')
        self.assertEqual(wrong.status_code, 401)
        self.assertIn('password is incorrect', wrong.json()['detail'])
        self.assertEqual(self.sign('login', n=2).status_code, 200)  # the right password still signs in

    def test_approving_sends_the_owners_approval_template(self):
        sent = []
        with patch.object(server, 'email_transport', return_value='brevo'), \
             patch.object(server, '_send_text_email', side_effect=lambda to, subject, body, transport: sent.append((to, subject, body))):
            self.sign('create')
            code = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'}).json()['code']['code']
            csrf = self.login()
            sent.clear()
            self.assertEqual(self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf).status_code, 200)
        (to, subject, body), = [mail for mail in sent if mail[0] == 'buyer@example.test']
        self.assertEqual(subject, 'Your AVARA Pro is approved ✅')
        self.assertTrue(body.startswith('Hi Buyer,\n\nThanks for your payment! Your order ' + code + ' is now approved.'))
        for line in ('Keep AVARA open and online, and it will unlock in a few seconds.', 'Plan: Pro', 'Active until: ',
                     "If it doesn't unlock, just reply to this email and I'll sort it out.", 'Welcome to AVARA!',
                     'AVARA · Your desktop. Your AI.'):
            self.assertIn(line, body)

    def test_hold_and_cancel_send_the_owners_templates(self):
        sent = []
        with patch.object(server, 'email_transport', return_value='brevo'), \
             patch.object(server, '_send_text_email', side_effect=lambda to, subject, body, transport: sent.append((to, subject, body))):
            self.sign('create')
            code = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'}).json()['code']['code']
            csrf = self.login()
            order_id = self.client.get('/api/owner/license-orders', params={'status': 'pending'}).json()['orders'][0]['id']
            sent.clear()
            held = self.owner_post(f'/api/owner/license-orders/{order_id}/hold',
                                   {'reason': "I'm still checking your payment.", 'need': 'your UPI reference'}, csrf)
            self.assertEqual(held.status_code, 200, held.text)
            self.assertTrue(held.json()['emailed'])
            # Still waiting, so it can be approved later.
            self.assertEqual(self.client.get('/api/owner/license-orders', params={'status': 'pending'}).json()['orders'][0]['id'], order_id)
            cancelled = self.owner_post(f'/api/owner/license-orders/{order_id}/reject',
                                        {'reason': "payment wasn't received", 'refund_amount': '₹200', 'refund_to': 'your UPI ID',
                                         'refund_by': '25 November'}, csrf)
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
        hint = 'AVR-••••-' + code[-4:]
        (_, hold_subject, hold_body), (_, cancel_subject, cancel_body) = [m for m in sent if m[0] == 'buyer@example.test']
        self.assertEqual(hold_subject, f'Your AVARA order {hint} is on hold')
        self.assertIn("Thanks for your order! I've put it on hold for now because I'm still checking your payment.\n", hold_body)
        self.assertIn('What I need from you: your UPI reference.', hold_body)
        self.assertIn('your code expires 48 hours after it was created', hold_body)
        self.assertIn('Thanks for your patience,', hold_body)
        self.assertEqual(cancel_subject, f'Your AVARA order {hint} has been cancelled')
        self.assertIn(f"Your AVARA order {hint} for the Pro plan has been cancelled because payment wasn't received.", cancel_body)
        self.assertIn('Your payment of ₹200 will be refunded to your UPI ID by 25 November.', cancel_body)
        self.assertIn("If you think this was a mistake, reply to this email and I'll help.", cancel_body)

    def test_owner_list_mixes_website_and_app_orders_newest_first(self):
        self.sign('create')
        self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'})
        with server.db() as con:
            user_id = con.execute('SELECT id FROM users').fetchone()[0]
            con.execute("INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status,platform) VALUES "
                        "(10,?,?,'none','draft-1',?,'approved','mac')", (user_id, '6-Month Pro — ₹1,400/6 months', int(time.time()) - 3600))
            con.execute("INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status,platform,deleted_at) VALUES "
                        "(9,?,'Pro — ₹200/month','none','draft-1',?,'approved','windows',?)", (user_id, int(time.time()) - 7200, int(time.time())))
        self.login()
        orders = self.client.get('/api/owner/license-orders', params={'status': 'all'}).json()['orders']
        self.assertEqual([(o.get('source', 'app'), o['plan_name']) for o in orders], [('app', 'Pro'), ('website', '6-Month Pro')])
        self.assertEqual(orders[1]['price_inr'], 1400)
        self.assertEqual(self.client.get('/api/owner/license-orders', params={'status': 'approved'}).json()['orders'][0]['id'], 10)

    def test_owner_can_delete_finished_orders_but_not_running_ones(self):
        self.sign('create')
        self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'basic'})
        code = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'}).json()['code']['code']
        csrf = self.login()
        listed = {o['id']: o for o in self.client.get('/api/owner/license-orders', params={'status': 'all'}).json()['orders']}
        replaced, waiting = sorted(listed)
        self.assertEqual((listed[replaced]['status'], listed[replaced]['deletable']), ('superseded', True))
        self.assertFalse(listed[waiting]['deletable'])
        self.assertEqual(self.owner_post(f'/api/owner/orders/app/{waiting}/delete', {}, csrf).status_code, 409)
        self.assertEqual(self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf).status_code, 200)
        self.assertEqual(self.owner_post(f'/api/owner/orders/app/{waiting}/delete', {}, csrf).status_code, 409)  # plan running
        self.assertEqual(self.owner_post(f'/api/owner/orders/app/{replaced}/delete', {}, csrf).status_code, 200)
        remaining = [o['id'] for o in self.client.get('/api/owner/license-orders', params={'status': 'all'}).json()['orders']]
        self.assertEqual(remaining, [waiting])
        self.assertIn('order_deleted', self.audit_actions())

    def test_brevo_sends_from_a_verified_gmail_without_a_domain(self):
        captured = {}

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return b'{"messageId": "<abc@smtp-relay.mailin.fr>"}'

        def fake_urlopen(request, timeout=10):
            captured.update(url=request.full_url, headers={k.lower(): v for k, v in request.header_items()},
                            body=json.loads(request.data))
            return Reply()

        with patch.dict(os.environ, {'BREVO_API_KEY': 'brevo-test-key', 'BREVO_SENDER_EMAIL': 'AVARA <owner@example.test>'}), \
             patch.object(server.urllib.request, 'urlopen', side_effect=fake_urlopen):
            self.assertEqual(server.email_transport(), 'brevo')
            server._send_text_email('buyer@example.test', 'Hello', 'Body text', 'brevo')
        self.assertEqual(captured['url'], 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(captured['headers']['api-key'], 'brevo-test-key')
        self.assertEqual(captured['body']['sender'], {'name': 'AVARA', 'email': 'owner@example.test'})
        self.assertEqual(captured['body']['to'], [{'email': 'buyer@example.test'}])

    def test_new_app_order_emails_every_owner_and_the_customer(self):
        sent = []
        with patch.object(server, 'OWNER_EMAILS', ['owner.one@example.test', 'owner.two@example.test']), \
             patch.object(server, 'email_transport', return_value='resend'), \
             patch.object(server, '_send_text_email', side_effect=lambda to, subject, body, transport: sent.append((to, subject, body))):
            self.sign('create')
            order = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'})
            self.assertEqual(order.status_code, 200, order.text)
        recipients = {to for to, _, _ in sent}
        self.assertEqual(recipients, {'owner.one@example.test', 'owner.two@example.test', 'buyer@example.test'})
        owner_mail = next(body for to, subject, body in sent if to == 'owner.one@example.test')
        for detail in ('Customer name:', 'buyer@example.test', 'Pro', 'Placed at:', 'India time', 'Computer:', 'Order code:'):
            self.assertIn(detail, owner_mail)
        self.assertEqual(self.client.get('/api/plans').json()['owner_emails'], ['kasula.ashwath3180076@gmail.com', 'saitheonepiece@gmail.com'])

    def test_a_one_letter_typo_does_not_silently_make_a_second_account(self):
        self.assertEqual(self.sign('create', email='kasula.santhosh@example.test').status_code, 200)
        missing = self.sign('login', n=2, email='kasula.santosh@example.test')
        self.assertEqual(missing.status_code, 404)
        self.assertIn('but there is one for', missing.json()['detail'])
        self.assertNotIn('kasula.santhosh@example.test', missing.json()['detail'])  # the other email is masked
        blocked = self.sign('create', n=2, email='kasula.santosh@example.test')
        self.assertEqual(blocked.status_code, 409)
        self.assertIn('very similar email', blocked.json()['detail'])
        confirmed = self.client.post('/api/v2/account/create', json={'token': token(2), 'email': 'kasula.santosh@example.test',
                                                                      'password': 'a-long-password-1', 'name': 'Other',
                                                                      'platform': 'windows', 'allow_similar': True})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)  # a real different email can still be created

    def test_orders_require_a_signed_in_account(self):
        refused = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'basic'})
        self.assertEqual(refused.status_code, 403)
        self.assertIn('Sign in', refused.json()['detail'])
        created = self.sign('create')
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()['account'], {'name': 'Buyer', 'email': 'buyer@example.test'})
        self.assertEqual(self.sign('create', n=2).status_code, 409)          # Email already has an account.
        self.assertEqual(self.sign('login', n=2, password='wrong-password-123').status_code, 401)
        self.assertEqual(self.sign('login', n=2).status_code, 200)            # Same account, second computer.
        ok = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'basic'})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['account']['email'], 'buyer@example.test')
        with server.db() as con:  # The website and the app share one account table.
            self.assertEqual(con.execute("SELECT COUNT(*) FROM users WHERE email='buyer@example.test'").fetchone()[0], 1)
        self.assertIn('app_sign_in_failed', self.audit_actions())

    def test_computer_with_a_plan_stays_with_its_account(self):
        self.sign('create')
        code = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'}).json()['code']['code']
        csrf = self.login()
        self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf)
        other = self.sign('create', email='other@example.test', name='Other')
        self.assertEqual(other.status_code, 409)
        self.assertEqual(self.sign('login').status_code, 200)

    def test_customers_are_emailed_about_orders_and_plan_changes(self):
        sent = []
        self.sign('create')
        with patch.object(server, 'email_transport', return_value='resend'), \
             patch.object(server, '_send_text_email', side_effect=lambda to, subject, body, transport: sent.append((to, subject, body))):
            code = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'}).json()['code']['code']
            csrf = self.login()
            self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf)
            entitlement = self.client.get('/api/owner/entitlements', params={'q': 'buyer@example.test'}).json()['entitlements'][0]
            for action in ({'action': 'pause'}, {'action': 'resume'}, {'action': 'extend', 'days': 10},
                           {'action': 'change_plan', 'plan_id': 'basic'}, {'action': 'cancel'}):
                self.assertTrue(self.owner_post(f"/api/owner/entitlements/{entitlement['id']}", action, csrf).json()['emailed'])
            custom = self.owner_post('/api/owner/email', {'installation_id': licensing.installation_id(token(1)),
                                                          'subject': 'Hello from AVARA', 'message': 'Thanks for your payment.'}, csrf)
            self.assertEqual(custom.status_code, 200, custom.text)
        owners = set(server.OWNER_EMAILS)
        self.assertEqual({to for to, _, _ in sent} - owners, {'buyer@example.test'})  # owners also get a new-order alert
        sent = [item for item in sent if item[0] not in owners]
        subjects = [subject for _, subject, _ in sent]
        for expected in ('Your AVARA order: Pro', 'Your AVARA Pro is approved ✅', 'Your AVARA plan is paused', 'Your AVARA plan is active again',
                         'Your AVARA plan was extended', 'Your AVARA plan changed to Basic', 'Your AVARA plan was cancelled', 'Hello from AVARA'):
            self.assertIn(expected, subjects)
        self.assertIn(code, sent[0][2])  # The order email repeats the one-time code.
        log = self.client.get('/api/owner/emails').json()
        self.assertEqual(len([row for row in log['emails'] if row['to'] not in owners]), 8)
        self.assertTrue(all(row['status'] == 'sent' for row in log['emails']))

    def test_emails_are_logged_when_sending_is_not_set_up(self):
        self.sign('create')
        with patch.object(server, 'email_transport', return_value=None):
            self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'basic'})
            csrf = self.login()
            log = self.client.get('/api/owner/emails').json()
        self.assertFalse(log['configured'])
        self.assertEqual(log['emails'][0]['status'], 'not_configured')
        with patch.object(server, 'email_transport', return_value=None):
            # The owner can write to any address, not only to customers with an account.
            anyone = self.owner_post('/api/owner/email', {'email': 'nobody@example.test', 'name': 'Ravi', 'subject': 's', 'message': 'm'}, csrf)
            invalid = self.owner_post('/api/owner/email', {'email': 'not-an-address', 'subject': 's', 'message': 'm'}, csrf)
            log = self.client.get('/api/owner/emails').json()
        self.assertEqual(anyone.status_code, 200, anyone.text)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual((log['emails'][0]['to'], log['emails'][0]['status']), ('nobody@example.test', 'not_configured'))

    def test_recent_filter_keeps_just_approved_orders_visible(self):
        self.sign('create')
        code = self.client.post('/api/v2/installations/register', json={'token': token(1), 'plan_id': 'pro'}).json()['code']['code']
        csrf = self.login()
        before = self.client.get('/api/owner/license-orders', params={'status': 'recent'}).json()['orders']
        self.assertEqual([o['status'] for o in before], ['pending'])
        self.owner_post('/api/owner/license-orders/approve', {'code': code}, csrf)
        after = self.client.get('/api/owner/license-orders', params={'status': 'recent'}).json()['orders']
        self.assertEqual([o['status'] for o in after], ['approved'])
        self.assertEqual(self.client.get('/api/owner/license-orders', params={'status': 'pending'}).json()['orders'], [])


if __name__ == '__main__':
    unittest.main()


class PassphraseSigningKeyTests(unittest.TestCase):
    PHRASE = 'my own avara secret signing sentence 2026'

    def test_owner_passphrase_with_spaces_signs_tickets_the_app_accepts(self):
        key = licensing.load_private_key(self.PHRASE)
        public = licensing.public_key_text(key)
        # Same key however the spaces are typed.
        for variant in ('  ' + self.PHRASE + '  ', self.PHRASE.replace(' ', '   '), self.PHRASE.replace(' ', '\t')):
            self.assertEqual(licensing.public_key_text(licensing.load_private_key(variant)), public)
        self.assertNotEqual(licensing.public_key_text(licensing.load_private_key(self.PHRASE.upper())), public)
        ticket = licensing.sign_ticket({'installation_id': 'ins_x', 'plan': 'pro', 'expires': 1, 'six_month': False, 'issued_at': 1}, key)
        self.assertEqual(licensing.verify_ticket(ticket, [public])['plan'], 'pro')
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
        import check_ticket_key
        self.assertEqual(check_ticket_key.public_from_private(self.PHRASE), public)

    def test_short_passphrases_are_refused_and_generated_keys_still_work(self):
        for weak in ('short words here', 'averyveryverylongsingleword_nospaces!', 'one two three'):
            with self.assertRaises(ValueError):
                licensing.load_private_key(weak)
        private, public = new_key_pair()
        self.assertEqual(licensing.public_key_text(licensing.load_private_key(private)), public)

    def test_server_signs_with_a_passphrase_from_render(self):
        public = licensing.public_key_text(licensing.load_private_key(self.PHRASE))
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'DB_PATH', str(Path(temp) / 's.db')), \
             patch.dict('os.environ', {'AVARA_TICKET_SIGNING_KEY': self.PHRASE, 'RENDER': ''}):
            server._signing_cache.clear()
            client = TestClient(server.app)
            body = client.post('/api/v2/activation/status', json={'token': token(1)}).json()
            self.assertEqual(licensing.verify_ticket(body['ticket'], [public])['state'], 'none')
            server._signing_cache.clear()
