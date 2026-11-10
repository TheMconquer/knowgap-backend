import unittest
from unittest.mock import AsyncMock, patch

from app import app


def setUpModule():
    #hese tests call rate limited routes more often than the limits allow
    limiter_off = patch.dict(app.config, {'QUART_RATE_LIMITER_ENABLED': False})
    limiter_off.start()
    unittest.addModuleCleanup(limiter_off.stop)

AUTH = {'Authorization': 'Bearer t'}
LINK = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'

ROUTES = [
    ('post', '/achieveup/skill-videos/get', {'course_id': '100', 'skill_name': 'Loops'}, 'get_skill_videos'),
    ('post', '/achieveup/skill-videos', {'course_id': '100', 'skill_name': 'Loops', 'link': LINK}, 'add_skill_video'),
    ('delete', '/achieveup/skill-videos/v-1', None, 'remove_skill_video'),
    ('post', '/achieveup/skill-videos/suggest', {'course_id': '100', 'skill_name': 'Loops'},
     'generate_ai_video_recommendations'),
    ('post', '/achieveup/skill-videos/v-1/find-moment', None, 'find_relevant_moment'),
    ('post', '/achieveup/skill-videos/v-1/verify', None, 'verify_ai_video'),
    ('post', '/achieveup/skill-videos/vote', {'video_id': 'v-1', 'vote_type': 'upvote'}, 'vote_skill_video'),
]

class SkillVideoRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def send(self, method, path, body=None, headers=AUTH, service_name=None, result=None, **kwargs):
        if body is not None:
            kwargs['json'] = body
        with patch(f'routes.skill_video_routes.{service_name}',
                   AsyncMock(return_value={'ok': True} if result is None else result)) as service:
            response = await getattr(app.test_client(), method)(path, headers=headers, **kwargs)
        return response, service

    async def test_login_is_required(self):
        for method, path, body, service_name in ROUTES:
            for headers in ({}, {'Authorization': 'Token t'}):
                with self.subTest(path=path, headers=headers):
                    response, service = await self.send(method, path, body, headers, service_name)
                    self.assertEqual(response.status_code, 401)
                    service.assert_not_called()

    async def test_video_id_from_the_path_reaches_the_service(self):
        for method, path, body, service_name in ROUTES:
            if body is not None:
                continue
            with self.subTest(path=path):
                response, service = await self.send(method, path, body, service_name=service_name)
                self.assertEqual(response.status_code, 200)
                service.assert_awaited_once_with('t', 'v-1')

    async def test_body_is_required(self):
        for method, path, body, service_name in ROUTES:
            if body is None:
                continue
            with self.subTest(path=path):
                response, service = await self.send(method, path, {}, service_name=service_name)
                self.assertEqual(response.status_code, 400)
                self.assertEqual((await response.get_json())['message'], 'Request body is required')
                service.assert_not_called()

    async def test_each_required_field(self):
        for method, path, body, service_name in ROUTES:
            if body is None:
                continue
            for field in body:
                with self.subTest(path=path, field=field):
                    response, service = await self.send(
                        method, path, {k: v for k, v in body.items() if k != field}, service_name=service_name)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual((await response.get_json())['error'], 'Missing required fields')
                    service.assert_not_called()

    async def test_service_errors_keep_their_status_and_message(self):
        error = {'error': 'No transcript available', 'message': 'This video has no captions to search.',
                 'statusCode': 422}
        for method, path, body, service_name in ROUTES:
            with self.subTest(path=path):
                response, _ = await self.send(method, path, body, service_name=service_name, result=error)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(await response.get_json(), error)

    async def test_error_without_a_status_is_500(self):
        response, _ = await self.send('post', '/achieveup/skill-videos/v-1/verify',
                                      service_name='verify_ai_video', result={'error': 'Video not found'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual((await response.get_json())['message'], 'Video not found')

    async def test_unexpected_errors_are_a_generic_500(self):
        for method, path, body, service_name in ROUTES:
            with self.subTest(path=path), \
                 patch(f'routes.skill_video_routes.{service_name}', AsyncMock(side_effect=Exception('secret detail'))), \
                 self.assertLogs('routes.skill_video_routes', 'ERROR'):
                kwargs = {'json': body} if body is not None else {}
                response = await getattr(app.test_client(), method)(path, headers=AUTH, **kwargs)
                self.assertEqual(response.status_code, 500)
                self.assertNotIn('secret detail', str(await response.get_json()))

    async def test_suggestions_pass_the_matrix(self):
        body = {'course_id': '100', 'skill_name': 'Loops', 'matrix_id': 'm-1'}
        _, service = await self.send('post', '/achieveup/skill-videos/suggest', body,
                                     service_name='generate_ai_video_recommendations')
        service.assert_awaited_once_with('t', '100', 'Loops', matrix_id='m-1')


if __name__ == '__main__':
    unittest.main()
