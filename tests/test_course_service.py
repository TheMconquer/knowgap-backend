import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import course_service
from utils import course_utils


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
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CourseUtilsTestCase(unittest.IsolatedAsyncioTestCase):
    def canvas(self, *responses):
        self.fake = FakeCanvas(*responses)
        patcher = patch('utils.course_utils.create_canvas_session', side_effect=lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)


class GetQuizzesTest(CourseUtilsTestCase):
    async def test_all_pages_are_read(self):
        self.canvas(FakeResponse(200, [{'id': 5, 'title': 'Quiz 1'}],
                                 headers={'Link': '<https://c.example/api/v1/courses/100/quizzes?page=2>; rel="next"'}),
                    FakeResponse(200, [{'id': 6, 'title': 'Quiz 2'}]))
        ids, names = await course_utils.get_quizzes('100', 'tok', 'https://c.example')
        self.assertEqual(ids, ['5', '6'])
        self.assertEqual(names, {'5': 'Quiz 1', '6': 'Quiz 2'})
        self.assertEqual(self.fake.urls, ['https://c.example/api/v1/courses/100/quizzes',
                                          'https://c.example/api/v1/courses/100/quizzes?page=2'])

    async def test_link_without_scheme(self):
        self.canvas(FakeResponse(200, []))
        await course_utils.get_quizzes('100', 'tok', 'c.example')
        self.assertEqual(self.fake.urls, ['https://c.example/api/v1/courses/100/quizzes'])

    async def test_canvas_error_raises(self):
        self.canvas(FakeResponse(401))
        with self.assertRaisesRegex(Exception, 'Failed to fetch quizzes: 401'):
            await course_utils.get_quizzes('100', 'tok', 'c.example')


class GetQuizQuestionsTest(CourseUtilsTestCase):
    async def test_questions(self):
        self.canvas(FakeResponse(200, [{'id': 1}]))
        self.assertEqual(await course_utils.get_quiz_questions('100', '5', 'tok', 'http://c.example'), [{'id': 1}])
        self.assertEqual(self.fake.urls, ['https://c.example/api/v1/courses/100/quizzes/5/questions'])

    async def test_errors_raise_with_the_status(self):
        for status in (403, 500):
            with self.subTest(status=status):
                self.canvas(FakeResponse(status, text_payload='denied'))
                with self.assertRaisesRegex(Exception, str(status)):
                    await course_utils.get_quiz_questions('100', '5', 'tok', 'c.example')


class GetCourseNameTest(CourseUtilsTestCase):
    async def test_course_name(self):
        self.canvas(FakeResponse(200, {'name': 'Intro'}))
        self.assertEqual(await course_utils.get_course_name('100', 'tok', 'c.example'), 'Intro')
        self.assertEqual(self.fake.urls, ['https://c.example/api/v1/courses/100'])

    async def test_course_without_a_name(self):
        self.canvas(FakeResponse(200, {}))
        self.assertEqual(await course_utils.get_course_name('100', 'tok', 'c.example'), 'Course name not found')

    async def test_canvas_error_is_none(self):
        self.canvas(FakeResponse(404))
        with patch('builtins.print'):
            self.assertIsNone(await course_utils.get_course_name('100', 'tok', 'c.example'))


class SmallHelpersTest(unittest.TestCase):
    def test_clean_text_keeps_only_ascii(self):
        self.assertEqual(course_utils.clean_text('café – naïve ✓ ok'), 'caf  nave  ok')

    def test_course_id_filter_matches_text_and_number(self):
        for course_id, values in [
            ('100', ['100', 100]),
            (100, ['100', 100]),
            ('0042', ['0042', 42]),
            ('abc', ['abc']),
            ('１２', ['１２']),   # full-width digits aren't a Canvas id
            ('²', ['²']),
            ('-5', ['-5']),
        ]:
            with self.subTest(course_id=course_id):
                self.assertEqual(course_utils.course_id_filter(course_id), {'course_id': {'$in': values}})


class UpdateQuizQuestionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.quizzes = AsyncMock(return_value=(['5', '6'], {'5': 'Quiz 1', '6': 'Quiz 2'}))
        self.course_name = AsyncMock(return_value='Intro')
        self.questions = {'5': [{'id': 1, 'question_text': '<p>Café <b>loops</b>?</p>',
                                 'question_type': 'essay_question'},
                                {'id': 2, 'question_text': 'No type'}],
                          '6': [{'id': 3, 'question_text': 'Second quiz'}]}

        async def questions(course_id, quiz_id, access_token, link):
            result = self.questions[quiz_id]
            if isinstance(result, Exception):
                raise result
            return result
        self.collection = MagicMock()
        self.collection.update_one = AsyncMock()
        for target, value in [
            ('services.course_service.get_quizzes', self.quizzes),
            ('services.course_service.get_course_name', self.course_name),
            ('services.course_service.get_quiz_questions', questions),
            ('services.course_service.quizzes_collection', self.collection),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def update(self):
        return await course_service.update_quiz_questions_per_course('100', 'tok', 'https://c.example')

    def saved(self):
        return [(c.args[0], c.args[1]['$set']) for c in self.collection.update_one.await_args_list]

    async def test_questions_are_saved_as_plain_ascii_text(self):
        result = await self.update()
        self.assertEqual(result, {'status': 'Success', 'message': 'Quiz questions updated successfully'})
        query, values = self.saved()[0]
        self.assertEqual(query, {'quizid': 5, 'courseid': '100', 'questionid': '1'})
        self.assertEqual((values['question_text'], values['question_type'], values['course_name']),
                         ('Caf loops?', 'essay_question', 'Intro'))
        self.assertEqual(self.saved()[1][1]['question_type'], 'unknown')
        self.assertEqual(len(self.saved()), 3)
        self.quizzes.assert_awaited_once_with('100', 'tok', 'c.example')

    async def test_inaccessible_quiz_does_not_stop_the_others(self):
        self.questions['5'] = Exception('403 Forbidden - No permission')
        result = await self.update()
        self.assertEqual(result['status'], 'Success')
        self.assertEqual([query['questionid'] for query, _ in self.saved()], ['3'])

    async def test_one_bad_question_does_not_stop_the_quiz(self):
        self.questions['5'] = [{'id': 1}, {'id': 2, 'question_text': 'Fine'}]  # first has no text
        await self.update()
        self.assertEqual([query['questionid'] for query, _ in self.saved()], ['2', '3'])

    async def test_course_without_quizzes(self):
        self.quizzes.return_value = ([], {})
        self.assertEqual(await self.update(), {'status': 'Success', 'message': 'No quizzes found'})
        self.course_name.assert_not_awaited()

    async def test_course_name_is_required(self):
        self.course_name.return_value = None
        self.assertEqual(await self.update(), {'status': 'Error', 'error': 'Failed to get course name'})
        self.collection.update_one.assert_not_awaited()

    async def test_quiz_list_failure_is_an_error_result(self):
        self.quizzes.side_effect = Exception('Failed to fetch quizzes: 401')
        self.assertEqual(await self.update(), {'status': 'Error', 'error': 'Failed to fetch quizzes: 401'})


if __name__ == '__main__':
    unittest.main()
