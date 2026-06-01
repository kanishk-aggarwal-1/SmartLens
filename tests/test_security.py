import unittest
import tempfile
from pathlib import Path

from starlette.testclient import TestClient

import app.main as main_mod
from app.services.security import hash_secret, read_session_token, verify_secret
from app.services.users import create_user, init_user_store


class SecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_require_auth = main_mod.settings.REQUIRE_AUTH
        self.original_session_secret = main_mod.settings.SESSION_SECRET_KEY
        self.original_encryption_secret = main_mod.settings.SECRET_ENCRYPTION_KEY
        self.original_api_key_hashes = main_mod.settings.API_KEY_HASHES
        self.original_secure_cookies = main_mod.settings.SECURE_COOKIES
        self.original_api_limit = main_mod.settings.API_RATE_LIMIT_PER_MINUTE
        self.original_database_url = main_mod.settings.DATABASE_URL

        main_mod.settings.REQUIRE_AUTH = True
        main_mod.settings.SESSION_SECRET_KEY = "test-session-secret"
        main_mod.settings.SECRET_ENCRYPTION_KEY = "FrlyFH6JFw1q1yDZWrMs14pq68KEwX0kwSMRsQiN3hU="
        main_mod.settings.API_KEY_HASHES = hash_secret("test-api-key", salt=b"abcdef0123456789")
        main_mod.settings.SECURE_COOKIES = False
        main_mod.settings.API_RATE_LIMIT_PER_MINUTE = 120
        main_mod.settings.DATABASE_URL = f"sqlite:///{Path(self.tmpdir.name) / 'smartlens-test.db'}"
        init_user_store()
        create_user(
            email="user@example.com",
            password_hash=hash_secret("correct-password", salt=b"0123456789abcdef"),
            google_maps_api_key="maps-user-key",
            gemini_api_key="gemini-user-key",
        )
        self.client = TestClient(main_mod.app)

    def tearDown(self) -> None:
        main_mod.settings.REQUIRE_AUTH = self.original_require_auth
        main_mod.settings.SESSION_SECRET_KEY = self.original_session_secret
        main_mod.settings.SECRET_ENCRYPTION_KEY = self.original_encryption_secret
        main_mod.settings.API_KEY_HASHES = self.original_api_key_hashes
        main_mod.settings.SECURE_COOKIES = self.original_secure_cookies
        main_mod.settings.API_RATE_LIMIT_PER_MINUTE = self.original_api_limit
        main_mod.settings.DATABASE_URL = self.original_database_url
        self.tmpdir.cleanup()

    def test_hash_verify_round_trip(self):
        stored = hash_secret("secret", salt=b"0123456789abcdef")
        self.assertTrue(verify_secret("secret", stored))
        self.assertFalse(verify_secret("wrong", stored))

    def test_protected_api_requires_auth_when_enabled(self):
        res = self.client.post("/api/translate", json={"text": "hello", "target_lang": "es"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_api_key_allows_protected_api(self):
        original_mode = main_mod.settings.AI_MODE
        main_mod.settings.AI_MODE = "mock"
        try:
            res = self.client.post(
                "/api/translate",
                headers={"X-API-Key": "test-api-key"},
                json={"text": "hello", "target_lang": "es"},
            )
        finally:
            main_mod.settings.AI_MODE = original_mode

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["mode"], "mock")

    def test_login_sets_signed_session_cookie(self):
        res = self.client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "correct-password"},
        )

        self.assertEqual(res.status_code, 200)
        cookie = res.cookies.get("smartlens_session")
        self.assertTrue(cookie)
        self.assertTrue(read_session_token(cookie))

    def test_bad_login_is_rejected(self):
        res = self.client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "wrong"},
        )

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["detail"]["code"], "INVALID_CREDENTIALS")

    def test_security_headers_are_present(self):
        res = self.client.get("/health")
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(res.headers.get("x-frame-options"), "DENY")
        self.assertIn("geolocation", res.headers.get("permissions-policy", ""))

    def test_prometheus_metrics_requires_auth(self):
        res = self.client.get("/metrics/prometheus")
        self.assertEqual(res.status_code, 401)

        authed = self.client.get("/metrics/prometheus", headers={"X-API-Key": "test-api-key"})
        self.assertEqual(authed.status_code, 200)
        self.assertIn("smartlens_request_count", authed.text)

    def test_signup_creates_account_and_session(self):
        res = self.client.post(
            "/auth/signup",
            json={
                "email": "new@example.com",
                "password": "new-password",
                "google_maps_api_key": "new-maps-key",
                "gemini_api_key": "new-gemini-key",
            },
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["email"], "new@example.com")
        self.assertTrue(res.cookies.get("smartlens_session"))


if __name__ == "__main__":
    unittest.main()
