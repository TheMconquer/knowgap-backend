import unittest
from unittest.mock import AsyncMock, patch

from config import Config

FRONTEND = Config.FRONTEND_URL
OTHER_SITE = 'https://evil.example'
PREFLIGHT = {'Access-Control-Request-Method': 'POST',
             'Access-Control-Request-Headers': 'authorization,content-type'}


def behind_heroku():
    return patch.object(Config, 'BEHIND_PROXY', True)


class CorsTest(unittest.IsolatedAsyncioTestCase):
    """Only the allow-list in app.py decides which sites may call the API."""

    async def get(self, path='/', **headers):
        from app import app
        return await app.test_client().get(path, headers=headers)

    async def test_frontend_is_allowed(self):
        response = await self.get(Origin=FRONTEND)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), FRONTEND)
        self.assertIn('Origin', response.headers.get('Vary', ''))

    async def test_other_site_is_not_allowed(self):
        response = await self.get(Origin=OTHER_SITE)
        self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))

    async def test_no_wildcard_without_origin(self):
        response = await self.get()
        self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))

    async def test_credentials_are_never_allowed(self):
        # The frontend sends its token in a header, not a cookie.
        for origin in (FRONTEND, OTHER_SITE):
            with self.subTest(origin=origin):
                response = await self.get(Origin=origin)
                self.assertIsNone(response.headers.get('Access-Control-Allow-Credentials'))

    async def test_preflight_from_frontend(self):
        from app import app
        response = await app.test_client().options(
            '/auth/login', headers={'Origin': FRONTEND, **PREFLIGHT})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), FRONTEND)
        self.assertIn('POST', response.headers.get('Access-Control-Allow-Methods', ''))
        allowed_headers = response.headers.get('Access-Control-Allow-Headers', '').lower()
        self.assertIn('authorization', allowed_headers)
        self.assertIn('content-type', allowed_headers)
        self.assertEqual(response.headers.get('Access-Control-Max-Age'), '3600')

    async def test_preflight_from_other_site(self):
        from app import app
        response = await app.test_client().options(
            '/auth/login', headers={'Origin': OTHER_SITE, **PREFLIGHT})
        self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
        self.assertIsNone(response.headers.get('Access-Control-Allow-Methods'))

    async def test_error_responses_allow_frontend(self):
        # Without these headers the browser hides the error message and the
        # frontend sees a network error instead.
        from app import app
        failing = AsyncMock(side_effect=Exception('boom'))
        with patch('routes.auth_routes.achieveup_login', failing), \
             self.assertLogs('routes.auth_routes', 'ERROR'):
            response = await app.test_client().post(
                '/auth/login', json={'email': 'cors@ucf.edu', 'password': 'x'},
                headers={'Origin': FRONTEND})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), FRONTEND)

        response = await self.get('/no-such-route', Origin=FRONTEND)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), FRONTEND)


class SecurityHeadersTest(unittest.IsolatedAsyncioTestCase):

    async def get(self, path='/', **headers):
        from app import app
        return await app.test_client().get(path, headers=headers)

    def assert_security_headers(self, response):
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(response.headers.get('Content-Security-Policy'),
                         "default-src 'none'; frame-ancestors 'none'")
        self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')

    async def test_headers_on_success_and_errors(self):
        for path, status in [('/', 200), ('/no-such-route', 404), ('/auth/me', 401)]:
            with self.subTest(path=path):
                response = await self.get(path)
                self.assertEqual(response.status_code, status)
                self.assert_security_headers(response)

    async def test_headers_on_preflight(self):
        from app import app
        response = await app.test_client().options(
            '/auth/login', headers={'Origin': FRONTEND, **PREFLIGHT})
        self.assert_security_headers(response)

    async def test_no_hsts_or_redirect_off_heroku(self):
        # Locally the X-Forwarded-* headers come from the client, so they
        # change nothing.
        response = await self.get(**{'X-Forwarded-Proto': 'http'})
        self.assertEqual(response.status_code, 200)
        response = await self.get(**{'X-Forwarded-Proto': 'https'})
        self.assertIsNone(response.headers.get('Strict-Transport-Security'))

    async def test_hsts_over_https_on_heroku(self):
        with behind_heroku():
            response = await self.get(**{'X-Forwarded-Proto': 'https'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('Strict-Transport-Security'), 'max-age=31536000')
        self.assert_security_headers(response)


class HttpsRedirectTest(unittest.IsolatedAsyncioTestCase):

    async def send(self, method, path, forwarded_proto, **kwargs):
        from app import app
        headers = {'X-Forwarded-Proto': forwarded_proto, 'Origin': FRONTEND,
                   **kwargs.pop('headers', {})}
        with behind_heroku():
            return await getattr(app.test_client(), method)(path, headers=headers, **kwargs)

    async def test_plain_http_is_redirected_to_https(self):
        response = await self.send('get', '/?a=1&b=two', 'http')
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers['Location'], 'https://localhost/?a=1&b=two')
        self.assertIsNone(response.headers.get('Strict-Transport-Security'))

    async def test_post_keeps_its_method_and_runs_nothing(self):
        login = AsyncMock(return_value={'token': 't'})
        with patch('routes.auth_routes.achieveup_login', login):
            response = await self.send('post', '/auth/login', 'http',
                                       json={'email': 'redirect@ucf.edu', 'password': 'x'})
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers['Location'], 'https://localhost/auth/login')
        login.assert_not_awaited()

    async def test_redirect_ignores_forwarded_host(self):
        # Heroku doesn't set X-Forwarded-Host, so any value is the client's
        # and must not choose where the redirect goes.
        response = await self.send('get', '/', 'http',
                                   headers={'X-Forwarded-Host': 'evil.example'})
        self.assertEqual(response.headers['Location'], 'https://localhost/')

    async def test_only_herokus_scheme_counts(self):
        # Heroku's value comes last; an earlier one is whatever the client sent.
        response = await self.send('get', '/', 'http, https')
        self.assertEqual(response.status_code, 200)
        response = await self.send('get', '/', 'https, http')
        self.assertEqual(response.status_code, 308)

    async def test_scheme_is_case_insensitive(self):
        response = await self.send('get', '/', 'HTTP')
        self.assertEqual(response.status_code, 308)
        response = await self.send('get', '/', 'HTTPS')
        self.assertEqual(response.headers.get('Strict-Transport-Security'), 'max-age=31536000')

    async def test_https_is_served(self):
        response = await self.send('get', '/', 'https')
        self.assertEqual(response.status_code, 200)

    async def test_missing_scheme_is_served(self):
        from app import app
        with behind_heroku():
            response = await app.test_client().get('/')
        self.assertEqual(response.status_code, 200)


class ClientIpTest(unittest.IsolatedAsyncioTestCase):
    """The connection itself comes from 10.0.0.9 (Heroku's router, on Heroku)."""

    async def client_ip(self, behind_proxy, forwarded_for=None):
        from app import app
        from utils.security_utils import client_ip
        # The ASGI layer normally sets Remote-Addr from the connection.
        headers = {'Remote-Addr': '10.0.0.9'}
        if forwarded_for:
            headers['X-Forwarded-For'] = forwarded_for
        with patch.object(Config, 'BEHIND_PROXY', behind_proxy):
            async with app.test_request_context('/', headers=headers):
                return client_ip()

    async def test_heroku_uses_routers_entry(self):
        self.assertEqual(await self.client_ip(True, '6.6.6.6, 1.2.3.4'), '1.2.3.4')
        self.assertEqual(await self.client_ip(True, '1.2.3.4'), '1.2.3.4')

    async def test_heroku_without_forwarded_for_uses_connection(self):
        self.assertEqual(await self.client_ip(True), '10.0.0.9')

    async def test_local_ignores_forwarded_for(self):
        self.assertEqual(await self.client_ip(False, '1.2.3.4'), '10.0.0.9')


if __name__ == '__main__':
    unittest.main()
