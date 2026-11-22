import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from storefront import server


class ActivationUpgradeTests(unittest.TestCase):
    def test_existing_basic_device_can_receive_new_pro_order(self):
        key = 'test-owner-upgrade-key-at-least-24'
        owner = SimpleNamespace(headers={'Authorization': 'Bearer ' + key})
        token = 'ab' * 32
        code = 'BASIC-' + hashlib.sha256(bytes.fromhex(token)).hexdigest()[:32].upper()
        with TemporaryDirectory() as temp, patch.object(server, 'DATABASE_URL', ''), \
             patch.object(server, 'DB_PATH', str(Path(temp) / 'store.db')), \
             patch.dict('os.environ', {'AVARA_STORE_ADMIN_KEY': key}):
            with server.db() as con:
                con.executemany('INSERT INTO users VALUES (?,?,?,?,?)', [(1,'A','a@example.test',b'0',b'0'),(2,'B','b@example.test',b'0',b'0')])
                for number, plan, user, platform in [(1,'Basic',1,'mac'),(2,'Pro',1,'mac'),(3,'Pro',2,'mac'),(4,'Pro',1,'windows'),(5,'Pro',1,'mac')]:
                    con.execute("INSERT INTO orders(id,user_id,plan,platform,addon,terms_version,created,status) VALUES (?,?,?,?,'None','test',0,'approved')",(number,user,plan,platform))
            server.activate_installation(1, server.ActivationApproval(code=code), owner)
            self.assertEqual(server.check_installation(server.InstallationCheck(token=token),Response())['plan'],'basic')
            for number in (3,4):
                with self.assertRaises(HTTPException):
                    server.activate_installation(number, server.ActivationApproval(code=code,replace_existing=True), owner)
            with self.assertRaises(HTTPException) as error:
                server.activate_installation(2, server.ActivationApproval(code=code), owner)
            self.assertEqual(error.exception.detail['code'],'installation_already_linked')
            self.assertEqual(error.exception.detail['existing_order_id'],1)
            server.delete_order(1, owner)
            self.assertEqual(server.check_installation(server.InstallationCheck(token=token),Response())['status'],'cancelled')
            # Refusing the explicit upgrade leaves the original entitlement intact.
            with self.assertRaises(HTTPException):
                server.activate_installation(2, server.ActivationApproval(code=code), owner)
            server.activate_installation(2, server.ActivationApproval(code=code,replace_existing=True), owner)
            self.assertEqual(server.check_installation(server.InstallationCheck(token=token),Response())['plan'],'pro')
            with server.db() as con:
                self.assertEqual(con.execute('SELECT status FROM orders WHERE id=1').fetchone()[0],'superseded')
                expiry = con.execute('SELECT expires FROM installations WHERE order_id=2').fetchone()[0]
            with self.assertRaises(HTTPException):
                server.activate_installation(2, server.ActivationApproval(code=code,replace_existing=True), owner)
            with server.db() as con:
                self.assertEqual(con.execute('SELECT expires FROM installations WHERE order_id=2').fetchone()[0],expiry)
            with self.assertRaises(HTTPException):
                server.activate_installation(1, server.ActivationApproval(code='BASIC-'+'1'*32), owner)
            server.activate_installation(5, server.ActivationApproval(code='BASIC-'+'2'*32), owner)
            with self.assertRaises(HTTPException):
                server.activate_installation(5, server.ActivationApproval(code=code,replace_existing=True), owner)
