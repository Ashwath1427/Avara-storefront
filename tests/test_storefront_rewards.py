"""Reward tests use a disposable database and never send email."""
import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from storefront import server


class RewardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(server, 'DB_PATH', str(Path(self.temp.name) / 'store.db'))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.auth = patch.object(server, 'session', lambda request: (request.user_id, 'Tester', f'test{request.user_id}@example.com'))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with server.reward_db() as con:
            for user_id in range(1, 4):
                con.execute('INSERT INTO users VALUES(?,?,?,?,?)', (user_id, 'Tester', f'test{user_id}@example.com', b'salt', b'hash'))

    def request(self, user_id=1):
        return SimpleNamespace(user_id=user_id)

    def puzzle(self):
        lock = server.reward_status(self.request())
        pin = ''.join(str(int(clue.split()[0]) - int(clue.split()[2])) for clue in lock['clues'])
        return server.RewardUnlock(lock_id=lock['lock_id'], pin=pin)

    def test_pin_is_global_single_use_and_receipt_survives_reopening(self):
        payload = self.puzzle()
        receipt = server.reward_unlock(payload, self.request())
        self.assertEqual(receipt, server.reward_status(self.request()))
        self.assertEqual(receipt['claim']['duration'], '1 month')
        with self.assertRaises(HTTPException) as error:
            server.reward_unlock(payload, self.request(2))
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            server.reward_unlock(payload, self.request())
        new_lock = server.reward_status(self.request(2))
        self.assertNotEqual(new_lock['lock_id'], payload.lock_id)
        with server.reward_db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM reward_claims').fetchone()[0], 1)

    def test_failed_attempts_persist_and_rate_limit(self):
        payload = self.puzzle()
        wrong = ('1' if payload.pin[0] != '1' else '2') + payload.pin[1:]
        for _ in range(5):
            with self.assertRaises(HTTPException) as error:
                server.reward_unlock(server.RewardUnlock(lock_id=payload.lock_id, pin=wrong), self.request())
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(HTTPException) as error:
            server.reward_unlock(payload, self.request())
        self.assertEqual(error.exception.status_code, 429)

    def test_simultaneous_correct_answers_have_exactly_one_winner(self):
        payload = self.puzzle()
        def attempt(user_id):
            try:
                server.reward_unlock(payload, self.request(user_id))
                return 'winner'
            except HTTPException as error:
                return error.status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [1, 2]))
        self.assertCountEqual(results, ['winner', 409])

    def test_reward_routes_require_signin_and_script_is_served(self):
        def deny(request):
            raise HTTPException(401, 'Please sign in first.')
        with patch.object(server, 'session', deny):
            with self.assertRaises(HTTPException) as error:
                server.reward_status(self.request())
            self.assertEqual(error.exception.status_code, 401)
            with self.assertRaises(HTTPException) as error:
                server.reward_unlock(server.RewardUnlock(lock_id='valid-lock-identifier', pin='123456'), self.request())
            self.assertEqual(error.exception.status_code, 401)
        response = server.reward_script()
        self.assertEqual(response.media_type, 'text/javascript')
        self.assertIn('sealed number lock', Path(response.path).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
