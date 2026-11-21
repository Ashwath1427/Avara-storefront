"""Owner deletion must work before activation and revoke existing delivery/access."""
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException, Response
from storefront import server


class OrderDeletionTests(unittest.TestCase):
    def test_delete_and_restore_before_and_after_activation(self):
        owner_key = 'test-owner-key-at-least-24-characters'
        owner = SimpleNamespace(headers={'Authorization': 'Bearer ' + owner_key})
        stranger = SimpleNamespace(headers={})
        token = 'ab' * 32
        code = hashlib.sha256(bytes.fromhex(token)).hexdigest()[:32].upper()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'DB_PATH', str(Path(directory) / 'store.db')), \
             patch.dict('os.environ', {'AVARA_STORE_ADMIN_KEY': owner_key}), \
             patch.object(server, 'session', return_value=(1, 'Test', 'test@example.test')), \
             patch.object(server, 'plan_bundle_ready', return_value=True), \
             patch.object(server, 'email_transport', return_value='test'):
            with server.db() as con:
                con.execute("INSERT INTO users VALUES (1,'Test','test@example.test',X'00',X'00')")
                for number, state in ((1, 'pending'), (2, 'approved'), (3, 'approved')):
                    con.execute("INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status) VALUES (?,1,'Pro','None','test',0,?)", (number, state))
            server.activate_installation(3, server.ActivationApproval(code='BASIC-' + code), owner)
            data = server.InstallationCheck(token=token)
            self.assertEqual(server.check_installation(data, Response())['status'], 'active')
            for route in (server.delete_order, server.restore_order):
                with self.assertRaises(HTTPException) as error:
                    route(2, stranger)
                self.assertEqual(error.exception.status_code, 401)
                with self.assertRaises(HTTPException) as error:
                    route(999, owner)
                self.assertEqual(error.exception.status_code, 404)
            for number in (1, 2, 3):
                server.delete_order(number, owner)
                server.delete_order(number, owner)  # Retry is safe.
                with self.assertRaises(HTTPException) as error:
                    server.download_order(number, owner)
                self.assertEqual(error.exception.status_code, 404)
                with self.assertRaises(HTTPException):
                    server.approve_order(number, owner, BackgroundTasks())
                with self.assertRaises(HTTPException):
                    server.change_subscription(number, server.SubscriptionChange(action='resume'), owner)
            self.assertEqual(server.customer_orders(owner)['orders'], [])
            self.assertEqual(server.admin_orders(owner)['orders'], [])
            deleted = server.admin_orders(owner, include_deleted=True)['orders']
            self.assertEqual(len(deleted), 3)
            self.assertTrue(all(order['deleted_at'] and not order['bundle_ready'] for order in deleted))
            self.assertEqual(server.check_installation(data, Response())['status'], 'cancelled')
            with self.assertRaises(HTTPException):
                server.activate_installation(2, server.ActivationApproval(code='BASIC-' + '1' * 32), owner)
            for number in (1, 2, 3):
                server.restore_order(number, owner)
            self.assertEqual(len(server.customer_orders(owner)['orders']), 3)
            self.assertEqual(server.check_installation(data, Response())['status'], 'active')
            with server.db() as con:
                con.execute('UPDATE installations SET expires=? WHERE order_id=3', (int(time.time()) - 1,))
            server.delete_order(3, owner)
            server.restore_order(3, owner)
            self.assertEqual(server.check_installation(data, Response())['status'], 'expired')
