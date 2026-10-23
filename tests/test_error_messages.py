import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import achieveup_service
from utils import youtube_utils

SECRET = 'secret internal detail'


class ErrorMessageTest(unittest.IsolatedAsyncioTestCase):
    """Unexpected errors are logged on the server, and the client only gets a
    generic message."""

    def assertNoDetail(self, payload):
        self.assertNotIn(SECRET, str(payload))

    async def test_import_routes_hide_exception_text(self):
        from app import app
        failing = AsyncMock(side_effect=Exception(SECRET))
        with patch('services.achieveup_auth_service.achieveup_verify_token', failing), \
             patch('services.achieveup_service.get_import_status', failing), \
             self.assertLogs('routes.achieveup_routes', 'ERROR') as logs:
            for method, path in [
                ('post', '/achieveup/matrix/import'),
                ('post', '/achieveup/skills/import'),
                ('get', '/achieveup/import-status/100'),
            ]:
                with self.subTest(path=path):
                    response = await getattr(app.test_client(), method)(
                        path, json={}, headers={'Authorization': 'Bearer t'})
                    self.assertEqual(response.status_code, 500)
                    body = await response.get_json()
                    self.assertEqual(body['message'], 'An unexpected error occurred')
                    self.assertNoDetail(body)
        self.assertEqual(sum(SECRET in line for line in logs.output), 3)

    async def test_skill_suggestions_hide_exception_text(self):
        with patch('services.achieveup_service.require_instructor', AsyncMock(side_effect=Exception(SECRET))), \
             patch('traceback.print_exc'):
            result = await achieveup_service.suggest_course_skills_ai('t', {})
        self.assertEqual(result['statusCode'], 500)
        self.assertNoDetail(result)

    async def test_skills_import_hides_exception_text(self):
        with patch('services.achieveup_service.require_course_instructor', AsyncMock(side_effect=Exception(SECRET))), \
             patch('traceback.print_exc'):
            result = await achieveup_service.import_skill_setting_from_course('1', '2', 'u', 't')
        self.assertEqual(result['statusCode'], 500)
        self.assertNoDetail(result)

    async def test_transcript_error_hides_exception_text(self):
        api = MagicMock()
        api.return_value.fetch.side_effect = Exception(SECRET)
        with patch('utils.youtube_utils.YouTubeTranscriptApi', api), patch('logging.error'):
            result = await youtube_utils.fetch_video_transcript('dQw4w9WgXcQ')
        self.assertEqual(result, {'success': False, 'error': 'Failed to fetch transcript'})


if __name__ == '__main__':
    unittest.main()
