import unittest
from unittest.mock import AsyncMock, patch

from app import app


def setUpModule():
    #These tests call rate-limited routes more often than the limits allow
    limiter_off = patch.dict(app.config, {'QUART_RATE_LIMITER_ENABLED': False})
    limiter_off.start()
    unittest.addModuleCleanup(limiter_off.stop)


AUTH = {'Authorization': 'Bearer t'}

#The student facing Canvas routes
ROUTES = [
    ('/canvas/courses', 'routes.canvas_routes.get_canvas_courses', ('t',)),
    ('/canvas/courses/100/quizzes', 'routes.canvas_routes.get_canvas_course_quizzes', ('t', '100')),
    ('/canvas/quizzes/5/questions', 'routes.canvas_routes.get_canvas_quiz_questions', ('t', '5')),
]

class CanvasRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def get(self, path, headers=AUTH):
        return await app.test_client().get(path, headers=headers)

    async def test_service_result_is_returned(self):
        for path, target, args in ROUTES:
            with self.subTest(path=path), patch(target, AsyncMock(return_value=[{'id': '1'}])) as service:
                response = await self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(await response.get_json(), [{'id': '1'}])
                service.assert_awaited_once_with(*args)

    async def test_login_is_required(self):
        for path, target, _ in ROUTES + [('/canvas/test-connection',
                                          'services.achieveup_auth_service.achieveup_verify_token', ())]:
            with self.subTest(path=path), patch(target, AsyncMock()) as service:
                response = await self.get(path, headers={})
                self.assertEqual(response.status_code, 401)
                service.assert_not_called()

    async def test_service_errors_keep_their_status(self):
        for path, target, _ in ROUTES:
            with self.subTest(path=path), \
                 patch(target, AsyncMock(return_value={'error': 'Canvas API token not found', 'statusCode': 400})):
                response = await self.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual((await response.get_json())['error'], 'Canvas API token not found')

    async def test_unexpected_errors_are_a_generic_500(self):
        for path, target, _ in ROUTES:
            with self.subTest(path=path), patch(target, AsyncMock(side_effect=Exception('secret detail'))):
                response = await self.get(path)
                self.assertEqual(response.status_code, 500)
                self.assertNotIn('secret detail', str(await response.get_json()))


class TestConnectionRouteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.verify = AsyncMock(return_value={'user': {'id': 'u1'}})
        self.canvas_token = AsyncMock(return_value='canvas-token')
        self.validate = AsyncMock(return_value={'valid': True, 'message': 'Token is valid',
                                                'user_info': {'id': 555, 'name': 'Ann'}})
        for target, value in [
            ('services.achieveup_auth_service.achieveup_verify_token', self.verify),
            ('services.achieveup_auth_service.get_user_canvas_token', self.canvas_token),
            ('services.achieveup_canvas_service.validate_canvas_token', self.validate),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def check(self):
        response = await app.test_client().get('/canvas/test-connection', headers=AUTH)
        return response.status_code, await response.get_json()

    async def test_connected(self):
        status, body = await self.check()
        self.assertEqual((status, body['connected'], body['user_info']), (200, True, {'id': 555, 'name': 'Ann'}))
        self.canvas_token.assert_awaited_once_with('u1')
        self.validate.assert_awaited_once_with('canvas-token')

    async def test_saved_token_no_longer_works(self):
        self.validate.return_value = {'valid': False, 'message': 'Invalid Canvas API token.'}
        self.assertEqual(await self.check(), (200, {'connected': False, 'message': 'Invalid Canvas API token.'}))

    async def test_no_saved_token(self):
        self.canvas_token.return_value = None
        status, body = await self.check()
        self.assertEqual((status, body['connected']), (200, False))
        self.validate.assert_not_awaited()

    async def test_invalid_login(self):
        self.verify.return_value = {'error': 'Token expired', 'statusCode': 401}
        status, _ = await self.check()
        self.assertEqual(status, 401)
        self.canvas_token.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
