import ssl
import unittest
from unittest.mock import MagicMock, patch

import aiohttp

import mongodb
from config import Config
from services.skill_tiers import is_mastered
from utils import course_utils
from utils.encryption_utils import decrypt_token, encrypt_token

KEY = bytes.fromhex(Config.HEX_ENCRYPTION_KEY)

class SkillTiersTest(unittest.TestCase):
    def test_mastered_starts_at_intermediate(self):
        for percentage, mastered in [(100, True), (75, True), (50, True), (49.99, False), (25, False), (0, False)]:
            with self.subTest(percentage=percentage):
                self.assertIs(is_mastered(percentage), mastered)


class EncryptionTest(unittest.TestCase):
    def test_round_trip(self):
        for token in ('c' * 64, '1~AbC/+=', 'ünïcödé token'):
            with self.subTest(token=token):
                self.assertEqual(decrypt_token(KEY, encrypt_token(KEY, token)), token)

    def test_same_token_encrypts_differently_each_time(self):
        first, second = encrypt_token(KEY, 'c' * 64), encrypt_token(KEY, 'c' * 64)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first[:16], second[:16])  # a fresh IV each time

    def test_ciphertext_hides_the_token(self):
        encrypted = encrypt_token(KEY, 'canvas-secret-token')
        self.assertNotIn(b'canvas-secret-token', encrypted)
        self.assertEqual(len(encrypted) % 16, 0)

    def test_wrong_key_or_damaged_data_does_not_decrypt(self):
        encrypted = encrypt_token(KEY, 'c' * 64)
        damaged = encrypted[:-1] + bytes([encrypted[-1] ^ 0xFF])
        for key, data in ((bytes(32), encrypted), (KEY, damaged)):
            with self.subTest(key=key[:2], data=data[-2:]):
                try:
                    self.assertNotEqual(decrypt_token(key, data), 'c' * 64)
                except (ValueError, UnicodeDecodeError):
                    pass


class CanvasSessionTest(unittest.IsolatedAsyncioTestCase):
    def test_development_verifies_with_certifi(self):
        with patch.object(Config, 'ENV', 'development'):
            context = course_utils.get_ssl_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_production_uses_default_verification(self):
        with patch.object(Config, 'ENV', 'production'):
            self.assertIs(course_utils.get_ssl_context(), True)

    async def test_sessions_always_verify_certificates(self):
        for env in ('development', 'production'):
            with self.subTest(env=env), patch.object(Config, 'ENV', env):
                session = course_utils.create_canvas_session()
                try:
                    self.assertIsInstance(session, aiohttp.ClientSession)
                    self.assertIsNot(session.connector._ssl, False)
                finally:
                    await session.close()


class MongoClientTest(unittest.TestCase):
    def setUp(self):
        saved = (mongodb._client, mongodb._db)

        def restore():
            mongodb._client, mongodb._db = saved
        self.addCleanup(restore)

    def connect(self, env):
        client = MagicMock()
        with patch('mongodb.AsyncIOMotorClient', return_value=client) as motor, \
             patch.object(Config, 'ENV', env), self.assertLogs('mongodb', 'INFO'):
            mongodb.initialize_db()
        return motor, client

    def test_production_requires_tls(self):
        motor, client = self.connect('production')
        kwargs = motor.call_args.kwargs
        self.assertEqual((kwargs['tls'], kwargs['tlsAllowInvalidCertificates']), (True, False))
        self.assertIs(mongodb.get_client(), client)
        self.assertIs(mongodb.get_db(), client[Config.DATABASE])

    def test_development_relaxes_tls(self):
        motor, _ = self.connect('development')
        kwargs = motor.call_args.kwargs
        self.assertEqual((kwargs['tls'], kwargs['tlsAllowInvalidCertificates']), (False, True))

    def test_bad_connection_settings_stop_the_app(self):
        with patch('mongodb.AsyncIOMotorClient', side_effect=Exception('bad URI')), \
             self.assertLogs('mongodb', 'ERROR'), self.assertRaises(SystemExit) as stop:
            mongodb.initialize_db()
        self.assertEqual(stop.exception.code, 69)

    def test_close_failure_is_logged(self):
        mongodb._client = MagicMock()
        mongodb._client.close.side_effect = Exception('already closed')
        with self.assertLogs('mongodb', 'ERROR'):
            mongodb.close_connection()


if __name__ == '__main__':
    unittest.main()
