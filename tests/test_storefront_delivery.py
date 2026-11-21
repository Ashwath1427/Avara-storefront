"""Approval and delivery tests use disposable data and never contact email providers."""
import gc
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException, Response
from storefront import server


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return b'{"id":"email_123"}'


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(server, 'DB_PATH', str(Path(self.temp.name) / 'store.db'))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        with server.db() as con:
            con.execute('INSERT INTO users VALUES(?,?,?,?,?)', (1, 'Buyer', 'buyer@example.com', b'salt', b'hash'))
            con.execute(
                'INSERT INTO orders(user_id,plan,addon,terms_version,created) VALUES(?,?,?,?,?)',
                (1, 'Basic — ₹150/month', 'None', 'draft-1', 100),
            )
        self.bundle = Path(self.temp.name) / 'avara-basic.zip'
        self.bundle.write_bytes(b'PK\x05\x06' + bytes(18))
        self.env = patch.dict('os.environ', {
            'AVARA_STORE_ADMIN_KEY': 'owner-key-with-at-least-24-characters',
            'AVARA_BASIC_ZIP': str(self.bundle),
            'RESEND_API_KEY': 'test-resend-key',
            'AVARA_STORE_PUBLIC_URL': 'https://avara.example',
            'SUPABASE_URL': '',
            'SUPABASE_SERVICE_ROLE_KEY': '',
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(gc.collect)

    def owner_request(self, key='owner-key-with-at-least-24-characters'):
        return SimpleNamespace(headers={'Authorization': 'Bearer ' + key}, base_url='https://avara.example/')

    def customer_request(self):
        return SimpleNamespace()

    def test_approved_order_exposes_activation_and_redeems_code_once(self):
        owner = self.owner_request()
        with server.db() as con:
            con.execute("UPDATE orders SET status='approved' WHERE id=1")
        listing = server.admin_orders(owner)
        self.assertIsNone(listing['orders'][0]['activation_expires'])
        token = '12' * 32
        digest = hashlib.sha256(bytes.fromhex(token)).hexdigest()[:32]
        approval = server.ActivationApproval(code='BASIC-' + digest)
        server.activate_installation(1, approval, owner)
        with self.assertRaises(HTTPException) as error:
            server.activate_installation(1, approval, owner)
        self.assertEqual(error.exception.status_code, 409)
        status = server.check_installation(server.InstallationCheck(token=token), Response())
        self.assertEqual(status['plan'], 'basic')
        self.assertEqual(server.admin_orders(owner)['orders'][0]['activation_expires'], status['expires'])

    def test_owner_can_pause_resume_change_renew_and_cancel_installation(self):
        owner = self.owner_request()
        token = '34' * 32
        digest = hashlib.sha256(bytes.fromhex(token)).hexdigest()[:32]
        with server.db() as con:
            con.execute("UPDATE orders SET status='approved' WHERE id=1")
        server.activate_installation(1, server.ActivationApproval(code='BASIC-' + digest), owner)

        server.change_subscription(1, server.SubscriptionChange(action='pause'), owner)
        self.assertEqual(
            server.check_installation(server.InstallationCheck(token=token), Response())['status'], 'paused'
        )
        server.change_subscription(1, server.SubscriptionChange(action='change_plan', plan='pro'), owner)
        server.change_subscription(1, server.SubscriptionChange(action='resume'), owner)
        active = server.check_installation(server.InstallationCheck(token=token), Response())
        self.assertEqual((active['status'], active['plan']), ('active', 'pro'))
        server.change_subscription(1, server.SubscriptionChange(action='change_plan', plan='six_month_plus'), owner)
        listing = server.admin_orders(owner)['orders'][0]
        self.assertTrue(listing['plan'].startswith('AVARA 6-Month Plus'))
        self.assertGreaterEqual(listing['activation_expires'], int(__import__('time').time()) + 180 * 86400)
        before = active['expires']
        server.change_subscription(1, server.SubscriptionChange(action='renew'), owner)
        self.assertGreater(
            server.check_installation(server.InstallationCheck(token=token), Response())['expires'], before
        )
        server.change_subscription(1, server.SubscriptionChange(action='cancel'), owner)
        listing = server.admin_orders(owner)['orders'][0]
        self.assertEqual((listing['activation_plan'], listing['activation_state']), ('pro', 'cancelled'))
        self.assertEqual(
            server.check_installation(server.InstallationCheck(token=token), Response())['status'], 'cancelled'
        )

    def test_owner_approval_requires_key_and_existing_plan_bundle(self):
        with self.assertRaises(HTTPException) as error:
            server.admin_orders(self.owner_request('wrong-key-that-is-still-long-enough'))
        self.assertEqual(error.exception.status_code, 401)

        tasks = BackgroundTasks()
        result = server.approve_order(1, self.owner_request(), tasks)
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(len(tasks.tasks), 2)
        with server.db() as con:
            row = con.execute('SELECT status,delivery_email_status FROM orders WHERE id=1').fetchone()
        self.assertEqual(row, ('approved', 'pending'))

    def test_customer_sees_only_owned_approved_download(self):
        with patch.object(server, 'session', lambda request: (1, 'Buyer', 'buyer@example.com')):
            with server.db() as con:
                con.execute("UPDATE orders SET status='approved' WHERE id=1")
            listing = server.customer_orders(self.customer_request())
            self.assertTrue(listing['orders'][0]['download_ready'])
            response = server.download_order(1, self.customer_request())
            self.assertEqual(Path(response.path), self.bundle)
            self.assertEqual(response.media_type, 'application/zip')

    def test_ready_email_targets_customer_and_contains_signin_and_guide_links(self):
        captured = {}

        def fake_open(request, timeout=0):
            captured['payload'] = json.loads(request.data)
            return FakeResponse()

        with patch('urllib.request.urlopen', fake_open):
            server._send_ready_email(1, 'Buyer', 'buyer@example.com', 'Basic — ₹150/month', 'https://avara.example', 'resend')
        payload = captured['payload']
        self.assertEqual(payload['to'], ['buyer@example.com'])
        self.assertIn('https://avara.example/#order', payload['text'])
        self.assertIn('https://avara.example/setup.html', payload['text'])
        self.assertIn('Ollama', payload['text'])

    def test_approval_refuses_to_send_when_bundle_is_missing(self):
        self.bundle.unlink()
        with self.assertRaises(HTTPException) as error:
            server.approve_order(1, self.owner_request(), BackgroundTasks())
        self.assertEqual(error.exception.status_code, 409)

    def test_checkout_accepts_launch_prices_and_rejects_old_prices(self):
        request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))
        with patch.object(server, 'session', lambda request: (1, 'Buyer', 'buyer@example.com')):
            with patch.dict('os.environ', {'AVARA_STORE_TEST_MODE': '1'}, clear=False):
                result = server.order(
                    server.Order(plan='Pro — ₹200/month', addon='None', accepted=True),
                    request,
                    BackgroundTasks(),
                )
                self.assertIn('Test order #', result['message'])
                six_month = server.order(
                    server.Order(plan='AVARA 6-Month Plus — ₹1,000/6 months', addon='None', accepted=True),
                    request,
                    BackgroundTasks(),
                )
                self.assertIn('Test order #', six_month['message'])
                with self.assertRaises(HTTPException) as error:
                    server.order(
                        server.Order(plan='Pro — ₹599/month', addon='None', accepted=True),
                        request,
                        BackgroundTasks(),
                    )
        self.assertEqual(error.exception.status_code, 400)

    def test_six_month_plus_activation_lasts_180_days_and_uses_pro_release(self):
        owner = self.owner_request()
        token = '56' * 32
        digest = hashlib.sha256(bytes.fromhex(token)).hexdigest()[:32]
        with server.db() as con:
            con.execute("UPDATE orders SET plan='AVARA 6-Month Plus — ₹1,000/6 months',status='approved' WHERE id=1")
        before = int(__import__('time').time())
        server.activate_installation(1, server.ActivationApproval(code='PRO-' + digest), owner)
        status = server.check_installation(server.InstallationCheck(token=token), Response())
        self.assertEqual(status['plan'], 'pro')
        self.assertGreaterEqual(status['expires'], before + 180 * 86400)
        self.assertEqual(server.plan_filename('AVARA 6-Month Plus — ₹1,000/6 months'), 'avara-pro.zip')

    def test_private_supabase_storage_can_supply_release_bundle(self):
        self.bundle.unlink()
        captured = {}

        def fake_open(request, timeout=0):
            captured['url'] = request.full_url
            captured['method'] = request.method
            return FakeResponse()

        with patch.dict('os.environ', {
            'SUPABASE_URL': 'https://project.supabase.co',
            'SUPABASE_SERVICE_ROLE_KEY': 'service-role-test-key',
            'AVARA_RELEASE_BUCKET': 'avara-releases',
        }, clear=False):
            with patch('urllib.request.urlopen', fake_open):
                self.assertTrue(server.plan_bundle_ready('Basic — ₹150/month'))
        self.assertEqual(captured['method'], 'HEAD')
        self.assertIn('/storage/v1/object/authenticated/avara-releases/avara-basic.zip', captured['url'])


if __name__ == '__main__':
    unittest.main()
