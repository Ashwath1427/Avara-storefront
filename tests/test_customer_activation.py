"""Exercise the desktop and storefront activation contract without a live deployment."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from backend import activation
from storefront import server


class CustomerActivationTests(unittest.TestCase):
    def setUp(self):
        mode = patch.dict("os.environ", {"AVARA_INSTALLER_DOWNLOADS": "false"})
        mode.start()
        self.addCleanup(mode.stop)

    def test_one_time_approval_and_plan_allowance(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "edition.txt"
            marker.write_text("basic", encoding="ascii")
            key = "owner-key-for-test-not-production"
            owner = SimpleNamespace(headers={"Authorization": "Bearer " + key})

            def check(request, timeout, **options):
                data = server.InstallationCheck(**json.loads(request.data))
                return BytesIO(json.dumps(server.check_installation(data, Response())).encode())

            with (
                patch.dict("os.environ", {"AVARA_STORE_ADMIN_KEY": key}),
                patch.object(server, "DB_PATH", str(root / "store.db")),
                patch.object(activation, "MARKER", marker),
                patch.object(activation, "STATE", root / "device"),
                patch.object(activation, "_next_check", 0),
                patch.object(activation, "_status", {"status": "pending"}),
                patch.object(activation, "urlopen", check),
            ):
                with server.db() as con:
                    con.execute("INSERT INTO users VALUES (1,'Test','test@example.test',X'00',X'00')")
                    con.executemany(
                        """INSERT INTO orders(id,user_id,plan,addon,terms_version,created,status)
                           VALUES (?,1,?,'None','test',0,?)""",
                        [(1, "Basic", "approved"), (2, "Pro", "approved"),
                         (3, "Basic", "pending"), (4, "Basic", "approved")],
                    )
                first = activation.status()
                self.assertEqual(first["status"], "pending")
                data = server.ActivationApproval(code=first["code"])
                for order, caller, expected in ((1, SimpleNamespace(headers={}), 401), (3, owner, 409)):
                    with self.assertRaises(HTTPException) as error:
                        server.activate_installation(order, data, caller)
                    self.assertEqual(error.exception.status_code, expected)
                server.activate_installation(1, data, owner)
                for order in (1, 4):
                    with self.assertRaises(HTTPException) as error:
                        server.activate_installation(order, data, owner)
                    self.assertEqual(error.exception.status_code, 409)
                activation._next_check = 0
                self.assertEqual(activation.status()["plan"], "basic")
                server.change_subscription(1, server.SubscriptionChange(action="pause"), owner)
                activation._next_check = 0
                self.assertEqual(activation.status()["status"], "paused")
                server.change_subscription(1, server.SubscriptionChange(action="change_plan", plan="pro"), owner)
                server.change_subscription(1, server.SubscriptionChange(action="resume"), owner)
                activation._next_check = 0
                self.assertEqual(activation.status()["plan"], "pro")
                server.change_subscription(1, server.SubscriptionChange(action="change_plan", plan="basic"), owner)
                activation._next_check = 0
                for _ in range(activation.LIMITS["website"]):
                    activation.allow("website")
                with self.assertRaises(PermissionError):
                    activation.allow("website")
                with self.assertRaises(PermissionError):
                    activation.allow("google_write")
                token = server.InstallationCheck(token="01" * 32)
                digits = hashlib.sha256(bytes.fromhex(token.token)).hexdigest()[:32]
                server.activate_installation(2, server.ActivationApproval(code="BASIC-" + digits), owner)
                self.assertEqual(server.check_installation(token, Response())["plan"], "pro")
                with server.db() as con:
                    con.execute("UPDATE installations SET expires=? WHERE order_id=1", (int(time.time()) - 1,))
                activation._next_check = 0
                self.assertEqual(activation.status()["status"], "expired")

    def test_six_month_basic_has_larger_allowance(self):
        with TemporaryDirectory() as temp:
            with patch.object(activation, 'STATE', Path(temp)), patch.object(activation, 'status', return_value={'status': 'active', 'plan': 'basic', 'six_month': True, 'expires': 12345}):
                for _ in range(activation.LIMITS['screen'] * 2):
                    activation.allow('screen')
                with self.assertRaises(PermissionError):
                    activation.allow('screen')
