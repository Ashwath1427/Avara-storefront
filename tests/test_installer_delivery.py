import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from types import SimpleNamespace
from storefront import server

class InstallerDeliveryTests(unittest.TestCase):
    def test_native_downloads_match_order_and_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {}
            for platform in server.INSTALLER_FILES:
                files[platform] = root / server.INSTALLER_FILES[platform][0]
                files[platform].write_bytes((platform + " installer fixture").encode() * 20)
            with patch.dict("os.environ", {"AVARA_INSTALLER_DOWNLOADS": "true"}), \
                 patch.object(server, "DB_PATH", str(root / "store.db")), \
                 patch.object(server, "session", return_value=(1, "Test", "test@example.test")), \
                 patch.object(server, "plan_bundle", side_effect=lambda plan, platform: files[platform]):
                with server.db() as con:
                    con.execute("INSERT INTO users VALUES (1,'Test','test@example.test',X'00',X'00')")
                    for number, (plan, platform) in enumerate(
                        [(plan, platform) for plan in ("Basic", "Pro") for platform in ("windows", "mac")], 1
                    ):
                        con.execute("INSERT INTO orders(id,user_id,plan,platform,addon,terms_version,created,status) VALUES (?,1,?,?,'None','test',0,'approved')", (number, plan, platform))
                request = SimpleNamespace(headers={})
                for number in range(1, 5):
                    variants = ("windows",) if number % 2 else ("mac-arm64", "mac-x86_64")
                    for platform in variants:
                        architecture = None if platform == "windows" else platform.removeprefix("mac-")
                        response = server.download_order(number, request, architecture=architecture)
                        self.assertEqual(Path(response.path).read_bytes(), files[platform].read_bytes())
                        self.assertEqual(response.filename, server.INSTALLER_FILES[platform][0])
                        self.assertEqual(response.media_type, server.INSTALLER_FILES[platform][1])
                for number, kwargs, status in [(2, {}, 400), (1, {"platform": "mac"}, 403)]:
                    with self.assertRaises(HTTPException) as error:
                        server.download_order(number, request, **kwargs)
                    self.assertEqual(error.exception.status_code, status)
                with patch.object(server, "session", return_value=(2, "Other", "other@example.test")):
                    with self.assertRaises(HTTPException) as error:
                        server.download_order(1, request)
                    self.assertEqual(error.exception.status_code, 404)
                with server.db() as con:
                    con.execute("UPDATE orders SET status='pending' WHERE id=1")
                with self.assertRaises(HTTPException) as error:
                    server.download_order(1, request)
                self.assertEqual(error.exception.status_code, 403)
