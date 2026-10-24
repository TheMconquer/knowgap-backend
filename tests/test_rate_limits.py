import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt

from config import Config
from services.achieveup_auth_service import JWT_ALGORITHM, JWT_SECRET
from utils import rate_limits
from utils.rate_limits import ExpiringMemoryStore, PAID_API_LIMITS

LOGIN_FAILED = {'error': 'Invalid email or password', 'statusCode': 401}


def token_for(user_id, **claims):
    payload = {'user_id': user_id, 'email': 'a@ucf.edu', 'role': 'instructor',
               'exp': datetime.now(timezone.utc) + timedelta(hours=1), **claims}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


class RateLimitTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # A fresh store per test, so counts from other tests don't carry over.
        patcher = patch.object(rate_limits.limiter, 'store', ExpiringMemoryStore())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def login(self, email, headers=None):
        from app import app
        with patch('routes.auth_routes.achieveup_login', AsyncMock(return_value=LOGIN_FAILED)):
            return await app.test_client().post(
                '/auth/login', json={'email': email, 'password': 'guess'}, headers=headers or {})

    async def test_login_limited_per_email(self):
        for _ in range(10):
            self.assertEqual((await self.login('ann@ucf.edu')).status_code, 401)
        response = await self.login('ann@ucf.edu')
        self.assertEqual(response.status_code, 429)
        body = await response.get_json()
        self.assertEqual(body['message'], 'Too many requests. Please wait a moment and try again.')
        self.assertEqual(body['statusCode'], 429)
        self.assertGreater(int(response.headers['Retry-After']), 0)
        # Another account from the same address is unaffected.
        self.assertEqual((await self.login('bob@ucf.edu')).status_code, 401)

    async def test_login_email_ignores_case_and_spaces(self):
        for _ in range(10):
            await self.login('ann@ucf.edu')
        self.assertEqual((await self.login('  ANN@ucf.edu ')).status_code, 429)

    async def test_unencodable_email_reaches_the_route(self):
        # A lone surrogate is valid JSON but not valid UTF-8.
        from app import app
        login = AsyncMock(return_value=LOGIN_FAILED)
        with patch('routes.auth_routes.achieveup_login', login):
            response = await app.test_client().post(
                '/auth/login', data='{"email": "\\ud800@ucf.edu", "password": "x"}',
                headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 401)
        login.assert_awaited_once()

    async def test_guesses_from_elsewhere_cannot_lock_out_the_owner(self):
        with patch.object(Config, 'BEHIND_PROXY', True):
            for _ in range(10):
                await self.login('ann@ucf.edu', {'X-Forwarded-For': '10.0.0.1'})
            blocked = await self.login('ann@ucf.edu', {'X-Forwarded-For': '10.0.0.1'})
            owner = await self.login('ann@ucf.edu', {'X-Forwarded-For': '10.0.0.2'})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(owner.status_code, 401)

    async def test_rate_limited_response_allows_frontend(self):
        for _ in range(10):
            await self.login('ann@ucf.edu')
        response = await self.login('ann@ucf.edu', {'Origin': Config.FRONTEND_URL})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), Config.FRONTEND_URL)

    async def test_preflights_are_not_counted(self):
        from app import app
        for _ in range(30):
            response = await app.test_client().options('/auth/login', headers={
                'Origin': Config.FRONTEND_URL, 'Access-Control-Request-Method': 'POST'})
            self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.login('ann@ucf.edu')).status_code, 401)

    async def signup(self, forwarded_for):
        from app import app
        with patch('routes.auth_routes.achieveup_signup', AsyncMock(return_value={'ok': True})), \
             patch.object(Config, 'BEHIND_PROXY', True):
            return await app.test_client().post('/auth/signup', json={
                'name': 'Ann', 'email': 'ann@ucf.edu', 'password': 'Passw0rd!',
                'canvasApiToken': 'x' * 64}, headers={'X-Forwarded-For': forwarded_for})

    async def test_spoofed_forwarded_for_does_not_escape_ip_limit(self):
        # The client controls every X-Forwarded-For entry except the last,
        # which Heroku's router adds.
        for i in range(60):
            self.assertEqual((await self.signup(f'6.6.{i}.1, 1.2.3.4')).status_code, 201)
        self.assertEqual((await self.signup('6.6.200.1, 1.2.3.4')).status_code, 429)
        self.assertEqual((await self.signup('5.6.7.8')).status_code, 201)

    async def validate_token(self, auth_token):
        from app import app
        verified = AsyncMock(return_value={'user': {'id': 'u1'}})
        with patch('routes.auth_routes.achieveup_verify_token', verified), \
             patch('services.achieveup_canvas_service.validate_canvas_token',
                   AsyncMock(return_value={'valid': True, 'message': 'Token is valid'})):
            return await app.test_client().post(
                '/auth/validate-canvas-token', json={'canvasApiToken': 'x' * 64},
                headers={'Authorization': f'Bearer {auth_token}'})

    async def test_user_limit_survives_logging_in_again(self):
        # Each login issues a new token, but the count is per user.
        for i in range(10):
            response = await self.validate_token(token_for('u1', n=i))
            self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.validate_token(token_for('u1', n=99))).status_code, 429)
        self.assertEqual((await self.validate_token(token_for('u2'))).status_code, 200)

    async def test_invalid_token_counts_by_ip(self):
        from utils.rate_limits import user_key
        from app import app
        for header in ('Bearer not-a-jwt', 'Bearer ' + jwt.encode({'user_id': 'u1'}, 'wrong-secret'),
                       'Bearer ' + token_for('u1', exp=datetime.now(timezone.utc) - timedelta(hours=1)),
                       'Basic abc', ''):
            with self.subTest(header=header):
                async with app.test_request_context(
                        '/', headers={'Authorization': header, 'Remote-Addr': '10.0.0.9'}):
                    self.assertEqual(await user_key(), 'ip:10.0.0.9')

    def test_paid_routes_are_limited(self):
        from app import app
        endpoints = [
            'achieveup.achieveup_ai_suggest_skills_route',
            'achieveup.achieveup_ai_analyze_questions_route',
            'achieveup.instructor_force_sync_route',
            'achieveup.update_channels_endpoint',
            'skill_video.add_skill_video_route',
            'skill_video.suggest_candidate_videos_route',
            'skill_video.find_relevant_moment_route',
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                limits = getattr(app.view_functions[endpoint], '_quart_rate_limiter_limits', [])
                self.assertEqual(limits, PAID_API_LIMITS)


class ExpiringMemoryStoreTest(unittest.IsolatedAsyncioTestCase):

    async def test_expired_entries_are_dropped(self):
        store = ExpiringMemoryStore()
        store.PRUNE_EVERY = 3
        now = datetime.now(timezone.utc)
        await store.set('old', now - timedelta(seconds=1))
        await store.set('current', now + timedelta(minutes=1))
        self.assertEqual(len(store._tats), 2)
        await store.set('new', now + timedelta(minutes=1))  # third set prunes
        self.assertEqual(set(store._tats), {'current', 'new'})
        self.assertEqual(await store.get('old', now), now)


if __name__ == '__main__':
    unittest.main()
