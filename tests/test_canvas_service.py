import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import aiohttp

from services import achieveup_canvas_service as canvas_service
from services.achieveup_canvas_service import CANVAS_API_URL, CANVAS_GRAPHQL_API_URL

USER = {'user': {'id': 'u1', 'role': 'student', 'canvasTokenType': 'student'}}

class FakeResponse:
    def __init__(self, status, json_payload=None, text_payload='', headers=None):
        self.status = status
        self._json_payload = json_payload
        self._text_payload = text_payload
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._json_payload

    async def text(self):
        return self._text_payload


class FakeCanvas:
    #Stands in for every Canvas session a test opens
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _next(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, headers=None, params=None):
        return self._next('GET', url, headers=headers, params=params)

    def post(self, url, headers=None, json=None):
        return self._next('POST', url, headers=headers, json=json)


class CanvasServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def canvas(self, *responses):
        """Answer the next Canvas requests with `responses`."""
        self.fake = FakeCanvas(*responses)
        patcher = patch('services.achieveup_canvas_service.create_canvas_session', side_effect=lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.fake


class NextLinkTest(unittest.TestCase):
    def test_next_link_is_found_in_any_position(self):
        header = ('<https://c.example/api/v1/x?page=1>; rel="current", '
                  '<https://c.example/api/v1/x?page=2>; rel="next", '
                  '<https://c.example/api/v1/x?page=9>; rel="last"')
        self.assertEqual(canvas_service._extract_next_canvas_link(header), 'https://c.example/api/v1/x?page=2')

    def test_no_next_link(self):
        for header in (None, '', '<https://c.example/api/v1/x?page=9>; rel="last"'):
            with self.subTest(header=header):
                self.assertIsNone(canvas_service._extract_next_canvas_link(header))


class FetchAllPagesTest(CanvasServiceTestCase):
    async def test_object_payload_counts_as_one_item_and_empty_page_adds_nothing(self):
        session = FakeCanvas(
            FakeResponse(200, {'id': 1}, headers={'Link': '<https://c.example/p2>; rel="next"'}),
            FakeResponse(200, []))
        items, error = await canvas_service._fetch_all_canvas_pages(session, 'https://c.example/p1', {}, {'a': 1})
        self.assertEqual((items, error), ([{'id': 1}], None))
        self.assertEqual([call['url'] for call in session.calls], ['https://c.example/p1', 'https://c.example/p2'])

    async def test_failure_on_a_later_page_returns_the_error_not_a_partial_list(self):
        session = FakeCanvas(
            FakeResponse(200, [{'id': 1}], headers={'Link': '<https://c.example/p2>; rel="next"'}),
            FakeResponse(503, text_payload='busy'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            items, error = await canvas_service._fetch_all_canvas_pages(
                session, 'https://c.example/p1', {}, resource='students')
        self.assertIsNone(items)
        self.assertEqual(error, {'error': 'Failed to fetch students: 503', 'statusCode': 503})


class ValidateCanvasTokenTest(CanvasServiceTestCase):
    async def test_student_token(self):
        canvas = self.canvas(FakeResponse(200, {'id': 555, 'name': 'Ann', 'email': 'ann@ucf.edu', 'extra': 1}))
        result = await canvas_service.validate_canvas_token('tok')
        self.assertEqual(result, {'valid': True, 'message': 'Token is valid',
                                  'user_info': {'id': 555, 'name': 'Ann', 'email': 'ann@ucf.edu'},
                                  'permissions': {}})
        self.assertEqual(canvas.calls[0]['url'], f'{CANVAS_API_URL}/users/self')
        self.assertEqual(canvas.calls[0]['headers']['Authorization'], 'Bearer tok')

    async def test_instructor_token_needs_a_teaching_enrollment(self):
        canvas = self.canvas(FakeResponse(200, {'id': 900}), FakeResponse(200, [{'id': 1}]))
        result = await canvas_service.validate_canvas_token('tok', 'instructor')
        self.assertTrue(result['valid'])
        self.assertEqual(result['permissions'], {'instructor': True})
        self.assertEqual(canvas.calls[1]['params']['enrollment_type'], 'teacher')

    async def test_instructor_token_without_courses(self):
        self.canvas(FakeResponse(200, {'id': 900}), FakeResponse(200, []))
        result = await canvas_service.validate_canvas_token('tok', 'instructor')
        self.assertFalse(result['valid'])
        self.assertIn('does not have instructor access', result['message'])

    async def test_instructor_course_lookup_fails(self):
        self.canvas(FakeResponse(200, {'id': 900}), FakeResponse(403, text_payload='forbidden'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.validate_canvas_token('tok', 'instructor')
        self.assertFalse(result['valid'])

    async def test_rejected_token(self):
        self.canvas(FakeResponse(401))
        result = await canvas_service.validate_canvas_token('tok')
        self.assertEqual(result, {'valid': False,
                                  'message': 'Invalid Canvas API token. Please check your token and try again.'})

    async def test_canvas_error(self):
        self.canvas(FakeResponse(500, text_payload='oops'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.validate_canvas_token('tok')
        self.assertEqual(result['message'], 'Canvas API returned error 500. Please try again later.')

    async def test_canvas_unreachable(self):
        self.canvas(aiohttp.ClientConnectionError('down'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.validate_canvas_token('tok')
        self.assertFalse(result['valid'])
        self.assertIn('Unable to connect', result['message'])


class CourseInstructorCheckTest(CanvasServiceTestCase):
    async def check(self, *responses):
        self.canvas(*responses)
        return await canvas_service.validate_canvas_instructor_for_course('tok', '100')

    async def test_teacher_enrollment(self):
        result = await self.check(FakeResponse(200, {'id': 900, 'name': 'Prof'}),
                                  FakeResponse(200, [{'type': 'TaEnrollment'}]))
        self.assertTrue(result['valid'])
        self.assertEqual(result['permissions'], {'instructor': True, 'course_id': '100',
                                                 'enrollment_type': 'TaEnrollment'})
        params = self.fake.calls[1]['params']
        self.assertEqual(self.fake.calls[1]['url'], f'{CANVAS_API_URL}/courses/100/enrollments')
        self.assertEqual(params['user_id'], 900)
        self.assertEqual(params['type[]'], ['TeacherEnrollment', 'TaEnrollment', 'DesignerEnrollment'])

    async def test_status_for_each_outcome(self):
        # Only "Canvas says no" is a 403; a bad token is the caller's 400 and
        # an outage keeps a 5xx, so callers never mistake one for the other.
        # A refused roster (401/403) and its own-enrollments fallback are in
        # test_skill_matrix_access.CourseInstructorCanvasRefusalTest.
        for responses, status in [
            ((FakeResponse(401),), 400),
            ((FakeResponse(200, {'id': 1}), FakeResponse(404)), 403),
            ((FakeResponse(200, {'id': 1}), FakeResponse(200, [])), 403),
        ]:
            with self.subTest(responses=responses):
                result = await self.check(*responses)
                self.assertFalse(result['valid'])
                self.assertEqual(result['statusCode'], status)

    async def test_canvas_outages_are_logged_and_keep_a_5xx(self):
        # A 5xx on the roster itself is in CourseInstructorCanvasRefusalTest.
        for responses, status in [
            ((FakeResponse(500, text_payload='x'),), 502),
            ((aiohttp.ClientConnectionError('down'),), 503),
            ((FakeResponse(200, {'id': 1}), asyncio.TimeoutError()), 503),
        ]:
            with self.subTest(responses=responses), \
                 self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
                result = await self.check(*responses)
                self.assertFalse(result['valid'])
                self.assertEqual(result['statusCode'], status)

    async def test_unexpected_error_is_500(self):
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await self.check(FakeResponse(200, None))  # .get on None fails
        self.assertEqual(result['statusCode'], 500)


class CourseStudentsTest(CanvasServiceTestCase):
    async def test_students_are_mapped_from_enrollments(self):
        self.canvas(FakeResponse(200, [
            {'enrollment_state': 'active',
             'user': {'id': 555, 'name': 'Ann Lee', 'email': 'ann@ucf.edu', 'sortable_name': 'Lee, Ann'}},
            {'enrollment_state': 'invited', 'user': {'id': 556, 'name': 'Bob'}},
        ]))
        students = await canvas_service.get_course_students('tok', 100)
        self.assertEqual(students[0], {'id': '555', 'name': 'Ann Lee', 'email': 'ann@ucf.edu',
                                       'sortable_name': 'Lee, Ann', 'enrollment_state': 'active',
                                       'course_id': '100'})
        self.assertEqual(students[1]['email'], '')
        self.assertEqual(self.fake.calls[0]['params']['type[]'], 'StudentEnrollment')

    async def test_roster_error_is_returned(self):
        self.canvas(FakeResponse(403, text_payload='no'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.get_course_students('tok', '100')
        self.assertEqual(result, {'error': 'Failed to fetch students: 403', 'statusCode': 403})


class InstructorCoursesMappingTest(CanvasServiceTestCase):
    async def test_course_fields(self):
        self.canvas(FakeResponse(200, [{'id': 1, 'name': 'My nickname', 'original_name': 'COP 3502',
                                        'course_code': 'COP3502', 'enrollment_term_id': 7}]))
        courses = await canvas_service.get_instructor_courses('tok')
        self.assertEqual(courses, [{'id': '1', 'name': 'My nickname', 'original_name': 'COP 3502',
                                    'code': 'COP3502', 'term': 7}])


class CourseNameTest(CanvasServiceTestCase):
    def test_official_name_prefers_original_name(self):
        self.assertEqual(canvas_service.official_course_name({'name': 'Nick', 'original_name': 'Real'}), 'Real')
        self.assertEqual(canvas_service.official_course_name({'name': 'Real'}), 'Real')
        self.assertIsNone(canvas_service.official_course_name({'name': ''}))

    async def test_course_name_lookup(self):
        self.canvas(FakeResponse(200, {'name': 'Nick', 'original_name': 'Intro Programming'}))
        self.assertEqual(await canvas_service.get_canvas_course_name('tok', '100'), 'Intro Programming')
        self.assertEqual(self.fake.calls[0]['url'], f'{CANVAS_API_URL}/courses/100')

    async def test_course_name_lookup_failures_are_none(self):
        for response in (FakeResponse(404, text_payload='missing'), aiohttp.ClientError('down')):
            with self.subTest(response=response):
                self.canvas(response)
                with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
                    self.assertIsNone(await canvas_service.get_canvas_course_name('tok', '100'))


class CourseDetailsTest(CanvasServiceTestCase):
    async def test_details(self):
        self.canvas(FakeResponse(200, {'id': 100, 'name': 'Intro', 'course_code': 'COP1000',
                                       'syllabus_body': '<p>Syllabus</p>', 'public_description': 'About',
                                       'image_download_url': 'https://img', 'total_students': 30}))
        result = await canvas_service.get_course_detailed_info('tok', '100')
        self.assertEqual(result['id'], '100')
        self.assertEqual(result['course_image'], 'https://img')
        self.assertEqual(result['public_description'], 'About')
        self.assertEqual(result['syllabus_body'], '<p>Syllabus</p>')
        self.assertIn('syllabus_body', self.fake.calls[0]['params']['include[]'])

    async def test_details_error(self):
        self.canvas(FakeResponse(404, text_payload='missing'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.get_course_detailed_info('tok', '100')
        self.assertEqual(result['statusCode'], 404)


def graphql_page(nodes, has_next=False, cursor=None):
    return FakeResponse(200, {'data': {'course': {'assignmentsConnection': {
        'nodes': nodes, 'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor}}}}})


class IsNewQuizTest(CanvasServiceTestCase):
    async def test_classic_quiz_matched_by_quiz_id(self):
        self.canvas(graphql_page([{'_id': '80', 'quiz': {'_id': '5'}, 'isNewQuiz': False}]))
        self.assertIs(await canvas_service.is_new_quiz('tok', '100', '5'), False)
        self.assertEqual(self.fake.calls[0]['url'], CANVAS_GRAPHQL_API_URL)
        self.assertEqual(self.fake.calls[0]['json']['variables'], {'course_id': '100', 'after': None})

    async def test_new_quiz_matched_by_assignment_id(self):
        # New Quizzes are assignments with no classic quiz behind them.
        self.canvas(graphql_page([{'_id': '81', 'quiz': None, 'isNewQuiz': True}]))
        self.assertIs(await canvas_service.is_new_quiz('tok', '100', '81'), True)

    async def test_later_pages_are_searched(self):
        self.canvas(graphql_page([{'_id': '1', 'quiz': None, 'isNewQuiz': False}], has_next=True, cursor='c1'),
                    graphql_page([{'_id': '81', 'quiz': None, 'isNewQuiz': True}]))
        self.assertIs(await canvas_service.is_new_quiz('tok', '100', '81'), True)
        self.assertEqual(self.fake.calls[1]['json']['variables']['after'], 'c1')

    async def test_unknown_quiz_is_none(self):
        self.canvas(graphql_page([{'_id': '1', 'quiz': {'_id': '2'}, 'isNewQuiz': False}]))
        self.assertIsNone(await canvas_service.is_new_quiz('tok', '100', '999'))

    async def test_canvas_errors_are_none(self):
        for response in (FakeResponse(200, {'errors': [{'message': 'nope'}]}), FakeResponse(500)):
            with self.subTest(response=response):
                self.canvas(response)
                with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
                    self.assertIsNone(await canvas_service.is_new_quiz('tok', '100', '5'))

    async def test_course_missing_from_reply_is_none(self):
        self.canvas(FakeResponse(200, {'data': {'course': None}}))
        self.assertIsNone(await canvas_service.is_new_quiz('tok', '100', '5'))


class InstructorQuizQuestionsTest(CanvasServiceTestCase):
    async def questions(self, quiz_type, *responses):
        self.canvas(*responses)
        with patch('services.achieveup_canvas_service.is_new_quiz', AsyncMock(return_value=quiz_type)):
            return await canvas_service.get_instructor_quiz_questions('tok', '5', '100')

    async def test_classic_questions(self):
        result = await self.questions(False, FakeResponse(200, [
            {'id': 1, 'question_type': 'text_only_question', 'question_text': 'Read this first'},
            {'id': 2, 'question_type': 'multiple_choice_question',
             'question_text': '<p>Pick</p><img src="https://c/f/9/preview" '
                              'data-api-endpoint="https://c/api/v1/courses/1/files/9">',
             'answers': [{'text': '<b>Yes</b>'}, {'text': '  '}, {'text': None}]},
        ]))
        self.assertEqual(len(result), 1)  # text-only items aren't questions
        self.assertEqual(result[0], {
            'id': '2', 'quiz_id': '5',
            'question_text': '<p>Pick</p><img src="https://c/f/9/preview" '
                             'data-api-endpoint="https://c/api/v1/courses/1/files/9">',
            'attachment_ids': ['9'], 'attachment_urls': ['https://c/f/9/preview'],
            'answer_texts': ['yes'], 'question_key': 'pick attachment_9 yes'})
        self.assertEqual(self.fake.calls[0]['url'], f'{CANVAS_API_URL}/courses/100/quizzes/5/questions')

    async def test_new_quiz_items(self):
        result = await self.questions(True, FakeResponse(200, [
            {'id': 'stim', 'entry_type': 'Stimulus', 'entry': {'item_body': 'Passage'}},
            {'id': 'i1', 'entry_type': 'Item', 'entry': {'item_body': '<p>Define X</p>'}},
            {'id': 'i2', 'entry_type': 'Item', 'entry': None},
        ]))
        self.assertEqual(result, [
            {'id': 'i1', 'question_text': '<p>Define X</p>', 'quiz_id': '5', 'question_key': 'define x'},
            {'id': 'i2', 'question_text': None, 'quiz_id': '5', 'question_key': 'question_i2'},
        ])
        self.assertTrue(self.fake.calls[0]['url'].endswith('/api/quiz/v1/courses/100/quizzes/5/items'))

    async def test_new_quiz_error_reply(self):
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await self.questions(True, FakeResponse(200, {'errors': ['bad']}))
        self.assertIn('error', result)

    async def test_canvas_error_status_is_kept(self):
        for quiz_type in (False, True):
            with self.subTest(new_quiz=quiz_type), self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
                result = await self.questions(quiz_type, FakeResponse(403, text_payload='no'))
                self.assertEqual(result['statusCode'], 403)

    async def test_unknown_quiz_type_is_404(self):
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await self.questions(None)
        self.assertEqual(result['statusCode'], 404)


class InstructorCourseQuizzesTest(CanvasServiceTestCase):
    async def test_quizzes(self):
        self.canvas(FakeResponse(200, {'data': {'course': {'quizzesConnection': {'nodes': [
            {'_id': 5, 'title': 'Quiz 1'}, {'_id': '6'}]}}}}))
        result = await canvas_service.get_instructor_course_quizzes('tok', 100)
        self.assertEqual(result, [{'id': '5', 'title': 'Quiz 1', 'course_id': '100'},
                                  {'id': '6', 'title': '', 'course_id': '100'}])

    async def test_graphql_error_is_400(self):
        self.canvas(FakeResponse(200, {'errors': [{'message': 'nope'}]}))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.get_instructor_course_quizzes('tok', '100')
        self.assertEqual(result['statusCode'], 400)

    async def test_http_error_keeps_status(self):
        self.canvas(FakeResponse(401, text_payload='no'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.get_instructor_course_quizzes('tok', '100')
        self.assertEqual(result, {'error': 'Failed to fetch instructor quizzes: 401', 'statusCode': 401})


class TokenExpirationTest(CanvasServiceTestCase):
    async def test_expiration_is_naive_utc(self):
        self.canvas(FakeResponse(200, {'expires_at': '2027-01-15T05:00:00Z'}))
        self.assertEqual(await canvas_service.get_token_expiration('t' * 64), (200, datetime(2027, 1, 15, 5, 0)))

    async def test_offset_is_converted_to_utc(self):
        self.canvas(FakeResponse(200, {'expires_at': '2027-01-15T00:00:00-05:00'}))
        self.assertEqual(await canvas_service.get_token_expiration('t' * 64), (200, datetime(2027, 1, 15, 5, 0)))

    async def test_token_that_never_expires(self):
        self.canvas(FakeResponse(200, {'expires_at': None}))
        self.assertEqual(await canvas_service.get_token_expiration('t' * 64), (200, None))

    async def test_unknown_or_rejected_token_is_401(self):
        for status in (401, 404):
            with self.subTest(status=status):
                self.canvas(FakeResponse(status))
                self.assertEqual(await canvas_service.get_token_expiration('t' * 64), (401, None))

    async def test_other_failures(self):
        self.canvas(FakeResponse(503, text_payload='busy'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            self.assertEqual(await canvas_service.get_token_expiration('t' * 64), (503, None))
        self.canvas(aiohttp.ClientError('down'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            self.assertEqual(await canvas_service.get_token_expiration('t' * 64), (500, None))


class StudentCanvasViewsTest(CanvasServiceTestCase):
    """The /canvas/courses, quizzes and questions lookups made with the
    logged-in user's own stored Canvas token."""

    def setUp(self):
        self.verify = AsyncMock(return_value=USER)
        self.canvas_token = AsyncMock(return_value='canvas-token')
        for target, value in [
            ('services.achieveup_canvas_service.achieveup_verify_token', self.verify),
            ('services.achieveup_canvas_service.get_user_canvas_token', self.canvas_token),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_courses(self):
        self.canvas(FakeResponse(200, [{'id': 1, 'name': 'Intro', 'course_code': 'C1'}]))
        self.assertEqual(await canvas_service.get_canvas_courses('t'),
                         [{'id': '1', 'name': 'Intro', 'code': 'C1'}])
        self.assertEqual(self.fake.calls[0]['params']['enrollment_type'], 'student')
        self.assertEqual(self.fake.calls[0]['headers']['Authorization'], 'Bearer canvas-token')

    async def test_quizzes(self):
        self.canvas(FakeResponse(200, {'data': {'course': {'quizzesConnection': {'nodes': [
            {'_id': 5, 'title': 'Quiz 1'}]}}}}))
        self.assertEqual(await canvas_service.get_canvas_course_quizzes('t', '100'),
                         [{'id': '5', 'title': 'Quiz 1', 'course_id': '100'}])

    async def test_questions(self):
        self.canvas(FakeResponse(200, [{'id': 7, 'question_text': 'Q?', 'answers': ['secret']}]))
        self.assertEqual(await canvas_service.get_canvas_quiz_questions('t', '5'),
                         [{'id': '7', 'question_text': 'Q?', 'quiz_id': '5'}])

    async def test_each_view_needs_a_stored_canvas_token(self):
        self.canvas_token.return_value = None
        for call in (canvas_service.get_canvas_courses('t'),
                     canvas_service.get_canvas_course_quizzes('t', '100'),
                     canvas_service.get_canvas_quiz_questions('t', '5')):
            result = await call
            self.assertEqual(result['statusCode'], 400)
            self.assertEqual(result['error'], 'Canvas API token not found')

    async def test_each_view_returns_login_errors(self):
        self.verify.return_value = {'error': 'Token expired', 'statusCode': 401}
        for call in (canvas_service.get_canvas_courses('t'),
                     canvas_service.get_canvas_course_quizzes('t', '100'),
                     canvas_service.get_canvas_quiz_questions('t', '5')):
            self.assertEqual(await call, {'error': 'Token expired', 'statusCode': 401})
        self.canvas_token.assert_not_awaited()

    async def test_rejected_canvas_token_asks_user_to_update_it(self):
        for call in (lambda: canvas_service.get_canvas_courses('t'),
                     lambda: canvas_service.get_canvas_course_quizzes('t', '100'),
                     lambda: canvas_service.get_canvas_quiz_questions('t', '5')):
            self.canvas(FakeResponse(401, text_payload='no'))
            with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
                result = await call()
            self.assertEqual(result['statusCode'], 401)
            self.assertIn('update it in your profile', result['message'])

    async def test_other_canvas_errors_keep_their_status(self):
        self.canvas(FakeResponse(503, text_payload='busy'))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.get_canvas_courses('t')
        self.assertEqual(result['statusCode'], 503)

    async def test_graphql_error_on_quizzes_is_400(self):
        self.canvas(FakeResponse(200, {'errors': ['x']}))
        with self.assertLogs('services.achieveup_canvas_service', 'ERROR'):
            result = await canvas_service.get_canvas_course_quizzes('t', '100')
        self.assertEqual(result['statusCode'], 400)


if __name__ == '__main__':
    unittest.main()
