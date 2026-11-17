import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from config import Config
from services import canvas_submissions_service as submissions_service
from services.canvas_submissions_service import CANVAS_API_URL
from utils.text_utils import build_question_key, hash_text

NEW_QUIZ_API = Config.CANVAS_NEW_QUIZ_API_URL

class FakeResponse:
    def __init__(self, status=200, json_payload=None, text_payload='', headers=None):
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

    async def text(self, encoding=None):
        return self._text_payload


class FakeCanvas:
    #Every Canvas session the code opens

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

    def get(self, url, headers=None, params=None, data=None):
        return self._next('GET', url, params=params)

    def post(self, url, headers=None, params=None, data=None):
        return self._next('POST', url, data=data)


class SubmissionsTestCase(unittest.IsolatedAsyncioTestCase):
    def canvas(self, *responses):
        self.fake = FakeCanvas(*responses)
        patcher = patch('services.canvas_submissions_service.create_canvas_session', side_effect=lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.fake

    def no_waiting(self):
        self.sleep = AsyncMock()
        patcher = patch('asyncio.sleep', self.sleep)
        patcher.start()
        self.addCleanup(patcher.stop)


class CanvasRateLimitTest(SubmissionsTestCase):
    def setUp(self):
        self.no_waiting()

    async def check(self, remaining):
        headers = {} if remaining is None else {'X-Rate-Limit-Remaining': remaining}
        await submissions_service.check_rate_limit(FakeResponse(headers=headers))

    async def test_plenty_left_does_not_pause(self):
        for remaining in (None, '100', '700.5', 'not-a-number'):
            with self.subTest(remaining=remaining):
                await self.check(remaining)
        self.sleep.assert_not_awaited()

    async def test_getting_close_pauses_briefly(self):
        await self.check('99.9')
        self.sleep.assert_awaited_once_with(1)

    async def test_nearly_out_pauses_longer_the_less_is_left(self):
        with self.assertLogs('services.canvas_submissions_service', 'WARNING'):
            await self.check('49')
            await self.check('49.9')
            await self.check('0')
        self.assertEqual(self.sleep.await_args_list, [call(11), call(10), call(60)])


class ProcessSubmissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_submission_fields(self):
        result = await submissions_service.process_submission_data({
            'id': 10, 'user_id': 555, 'quiz_id': 5, 'attempt': 2, 'score': 8, 'kept_score': 9,
            'quiz_points_possible': 10, 'workflow_state': 'complete', 'user': {'display_name': 'Ann'},
        })
        self.assertEqual(
            {k: result[k] for k in ('submission_id', 'student_id', 'student_name', 'quiz_id', 'attempt',
                                    'score', 'kept_score', 'points_possible', 'workflow_state', 'questions')},
            {'submission_id': '10', 'student_id': '555', 'student_name': 'Ann', 'quiz_id': '5', 'attempt': 2,
             'score': 8, 'kept_score': 9, 'points_possible': 10, 'workflow_state': 'complete', 'questions': []})

    async def test_questions_are_keyed_by_their_content(self):
        question = {'id': 7, 'question_type': 'multiple_choice_question', 'question_text': '<p>2+2?</p>',
                    'answers': [{'text': '4'}, {'text': '5'}], 'points': 1, 'points_possible': 1,
                    'correct': True, 'answer': 1, 'correct_answer': 1}
        result = await submissions_service.process_submission_data(
            {'id': 1, 'user_id': 1, 'quiz_id': 5, 'questions': [question]})
        self.assertEqual(result['questions'], [{
            'question_id': hash_text(build_question_key('<p>2+2?</p>', ['4', '5'])),
            'canvas_question_id': '7', 'question_type': 'multiple_choice_question',
            'points': 1, 'points_possible': 1, 'correct': True, 'answer': 1, 'correct_answer': 1}])

    async def test_unanswered_question_counts_as_wrong(self):
        result = await submissions_service.process_submission_data(
            {'id': 1, 'user_id': 1, 'quiz_id': 5, 'questions': [{'id': 7, 'question_text': 'Q'}]})
        self.assertFalse(result['questions'][0]['correct'])

    async def test_unreadable_submission_is_empty(self):
        with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
            result = await submissions_service.process_submission_data({'id': 1, 'user': None})
        self.assertEqual(result, {})


class StoreSubmissionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.collection = MagicMock()
        self.collection.update_one = AsyncMock()
        patcher = patch('services.canvas_submissions_service.submissions_collection', self.collection)
        patcher.start()
        self.addCleanup(patcher.stop)

    def submission(self):
        return {'submission_id': '10', 'student_id': '555', 'quiz_id': '5', 'questions': []}

    async def test_nothing_is_cached_by_default(self):
        with patch.object(Config, 'ENABLE_SUBMISSION_CACHE', False):
            self.assertFalse(await submissions_service.store_submission_data('100', self.submission()))
        self.collection.update_one.assert_not_awaited()

    async def test_forced_or_enabled_cache_upserts_by_submission(self):
        for enabled, force in ((False, True), (True, False)):
            with self.subTest(enabled=enabled, force=force), patch.object(Config, 'ENABLE_SUBMISSION_CACHE', enabled):
                self.collection.update_one.reset_mock()
                self.assertTrue(await submissions_service.store_submission_data('100', self.submission(), force))
                query, update = self.collection.update_one.await_args.args
                self.assertEqual(query, {'submission_id': '10', 'student_id': '555', 'quiz_id': '5'})
                self.assertEqual(update['$set']['course_id'], '100')
                self.assertIn('cached_at', update['$set'])
                self.assertTrue(self.collection.update_one.await_args.kwargs['upsert'])

    async def test_write_failure_is_false(self):
        self.collection.update_one.side_effect = Exception('db down')
        with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
            self.assertFalse(await submissions_service.store_submission_data('100', self.submission(), True))


class AllSubmissionsTest(SubmissionsTestCase):
    def setUp(self):
        self.no_waiting()

    def quiz_type(self, value):
        patcher = patch('services.canvas_submissions_service.is_new_quiz', AsyncMock(return_value=value))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_classic_quiz_pages(self):
        self.quiz_type(False)
        canvas = self.canvas(
            FakeResponse(200, {'quiz_submissions': [{'id': 1}, {'id': 2}]},
                         headers={'Link': '<https://c.example/page2>; rel="next", <https://c.example/last>; rel="last"'}),
            FakeResponse(200, {'quiz_submissions': [{'id': 3}]}))
        result = await submissions_service.get_all_course_submissions('tok', '100', '5')
        self.assertEqual(result, {'submissions': [{'id': 1}, {'id': 2}, {'id': 3}], 'count': 3, 'is_new_quiz': False})
        self.assertEqual(canvas.calls[0]['url'], f'{CANVAS_API_URL}/courses/100/quizzes/5/submissions')
        self.assertEqual(canvas.calls[0]['params']['include[]'], ['submission', 'user'])
        self.assertEqual((canvas.calls[1]['url'], canvas.calls[1]['params']), ('https://c.example/page2', {}))

    async def test_new_quiz_submissions_are_mapped_to_classic_fields(self):
        self.quiz_type(True)
        canvas = self.canvas(FakeResponse(200, [
            {'id': 40, 'user_id': 555, 'attempt': 2, 'score': None, 'entered_score': 7,
             'submitted_at': '2026-10-01T10:00:00Z', 'workflow_state': 'graded', 'assignment_id': 81},
            {'id': 41, 'user_id': 556, 'attempt': None},  # never submitted
        ]))
        result = await submissions_service.get_all_course_submissions('tok', '100', '81')
        self.assertTrue(result['is_new_quiz'])
        self.assertEqual(result['count'], 1)
        submission = result['submissions'][0]
        self.assertEqual((submission['id'], submission['user_id'], submission['attempt'], submission['kept_score'],
                          submission['finished_at'], submission['quiz_id']),
                         (40, 555, 2, 7, '2026-10-01T10:00:00Z', 81))
        self.assertEqual(canvas.calls[0]['url'], f'{CANVAS_API_URL}/courses/100/assignments/81/submissions')

    async def test_new_quiz_pages(self):
        self.quiz_type(True)
        canvas = self.canvas(
            FakeResponse(200, [{'id': 1, 'attempt': 1}], headers={'Link': '<https://c.example/p2>; rel="next"'}),
            FakeResponse(200, [{'id': 2, 'attempt': 1}]))
        result = await submissions_service.get_all_course_submissions('tok', '100', '81')
        self.assertEqual(result['count'], 2)
        self.assertEqual(canvas.calls[1]['url'], 'https://c.example/p2')

    async def test_errors(self):

        tries = submissions_service.CANVAS_RETRIES + 1
        for quiz_type, responses, status in [
            (False, [FakeResponse(403, text_payload='no')], 403),
            (True, [FakeResponse(500, text_payload='oops')] * tries, 500),
            (True, [FakeResponse(200, {'errors': ['bad']})], 400),
        ]:
            with self.subTest(new_quiz=quiz_type, status=status):
                self.quiz_type(quiz_type)
                self.canvas(*responses)
                with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
                    result = await submissions_service.get_all_course_submissions('tok', '100', '5')
                self.assertEqual(result['statusCode'], status)

    async def test_unknown_quiz_type_is_404(self):
        self.quiz_type(None)
        self.canvas()
        with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
            result = await submissions_service.get_all_course_submissions('tok', '100', '5')
        self.assertEqual(result['statusCode'], 404)


ITEMS = [
    {'id': 'i2', 'position': 3, 'points_possible': 2, 'entry_type': 'Item', 'entry': {'item_body': '<p>Q2</p>'}},
    {'id': 'stim', 'position': 1, 'entry_type': 'Stimulus', 'entry': {'item_body': 'Passage'}},
    {'id': 'i1', 'position': 2, 'points_possible': 1, 'entry_type': 'Item', 'entry': {'item_body': '<p>Q1</p>'}},
]
HEADER = ['Name', 'ID', 'Attempt',
          'ItemID', 'Item', 'Answer', 'Points', 'Status',
          'ItemID', 'Item', 'Answer', 'Points', 'Status']
COMPLETED = {'progress': {'workflow_state': 'completed', 'results': {'url': 'https://files.example/report.csv'}}}


def csv_text(*rows, bom=False):
    lines = [','.join(HEADER)] + [','.join(row) for row in rows]
    return ('﻿' if bom else '') + '\n'.join(lines) + '\n'


def row(student, attempt, q1_points, q2_points, q1_status='Graded', q2_status='Graded'):
    return [f'Student {student}', student, attempt,
            'i1', 'Q1', 'a', q1_points, q1_status,
            'i2', 'Q2', 'b', q2_points, q2_status]


class NewQuizReportTest(SubmissionsTestCase):
    def setUp(self):
        self.no_waiting()

    async def report(self, report_text, items=ITEMS, progress=COMPLETED, extra=()):
        self.canvas(FakeResponse(200, items), FakeResponse(201, progress), *extra,
                    FakeResponse(200, text_payload=report_text))
        return await submissions_service.get_new_quiz_data('tok', '100', '81')

    async def test_results_per_student(self):
        result = await self.report(csv_text(row('555', '1', '1', '1'), row('556', '1', '0', '2')))
        self.assertEqual(result['results']['555'], [
            {'id': 'i1', 'question_text': '<p>Q1</p>', 'correct': True},
            {'id': 'i2', 'question_text': '<p>Q2</p>', 'correct': False},  # 1 of 2 points
        ])
        self.assertEqual([q['correct'] for q in result['results']['556']], [False, True])
        items_call, report_call, download_call = self.fake.calls
        self.assertEqual(items_call['url'], f'{NEW_QUIZ_API}/courses/100/quizzes/81/items')
        self.assertEqual(report_call['data'], {'quiz_report[report_type]': 'student_analysis',
                                               'quiz_report[format]': 'csv'})
        self.assertEqual(download_call['url'], 'https://files.example/report.csv')

    async def test_only_the_latest_attempt_counts(self):
        for rows in ([row('555', '1', '0', '0'), row('555', '2', '1', '2')],
                     [row('555', '2', '1', '2'), row('555', '1', '0', '0')]):
            with self.subTest(order=[r[2] for r in rows]):
                result = await self.report(csv_text(*rows))
                self.assertEqual([q['correct'] for q in result['results']['555']], [True, True])

    async def test_answers_waiting_for_grading_are_left_out(self):
        result = await self.report(csv_text(row('555', '1', '1', '0', q2_status='Pending Review')))
        self.assertEqual([q['id'] for q in result['results']['555']], ['i1'])

    async def test_byte_order_mark_is_ignored(self):
        result = await self.report(csv_text(row('555', '1', '1', '2'), bom=True))
        self.assertIn('555', result['results'])

    async def test_question_worth_no_points_is_never_correct(self):
        items = [dict(ITEMS[0], points_possible=0), ITEMS[1], ITEMS[2]]
        result = await self.report(csv_text(row('555', '1', '1', '0')), items=items)
        self.assertFalse(result['results']['555'][1]['correct'])

    async def test_report_is_polled_until_finished(self):
        queued = {'progress': {'workflow_state': 'queued', 'url': 'https://c.example/progress/1'}}
        result = await self.report(csv_text(row('555', '1', '1', '2')), progress=queued,
                                   extra=(FakeResponse(200, {'workflow_state': 'running', 'url': 'https://c.example/progress/1'}),
                                          FakeResponse(200, COMPLETED['progress'])))
        self.assertIn('555', result['results'])
        self.assertEqual(self.sleep.await_count, 2)
        self.assertEqual([c['url'] for c in self.fake.calls[2:4]], ['https://c.example/progress/1'] * 2)

    async def test_report_that_fails_or_has_no_file(self):
        for progress in ({'progress': {'workflow_state': 'failed'}},
                         {'progress': {'workflow_state': 'completed', 'results': {}}}):
            with self.subTest(progress=progress):
                self.canvas(FakeResponse(200, ITEMS), FakeResponse(200, progress))
                result = await submissions_service.get_new_quiz_data('tok', '100', '81')
                self.assertEqual(result, {'error': 'New quiz report did not finish.', 'statusCode': 502})

    async def test_report_columns_must_match_the_quiz(self):
        result = await self.report(csv_text(row('555', '1', '1', '2')), items=[ITEMS[2]])
        self.assertEqual(result['statusCode'], 502)
        self.assertIn('does not match', result['error'])

    async def test_download_errors(self):
        for response in (FakeResponse(403, text_payload='denied'),
                         FakeResponse(200, text_payload='{"errors": [{"message": "expired"}]}')):
            with self.subTest(status=response.status):
                self.canvas(FakeResponse(200, ITEMS), FakeResponse(200, COMPLETED), response)
                with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
                    result = await submissions_service.get_new_quiz_data('tok', '100', '81')
                self.assertEqual(result, {'error': 'Failed to download new quiz report.', 'statusCode': 502})

    async def test_request_errors(self):
        for responses, status in [((FakeResponse(404),), 404),
                                  ((FakeResponse(200, ITEMS), FakeResponse(403)), 403)]:
            with self.subTest(status=status):
                self.canvas(*responses)
                result = await submissions_service.get_new_quiz_data('tok', '100', '81')
                self.assertEqual(result['statusCode'], status)


class FakeMasteryDocs:
    def __init__(self, docs=()):
        self.docs = list(docs)
        self.count_documents = AsyncMock(side_effect=lambda query: len(self.docs))

    def find(self, query):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=list(self.docs))
        return cursor


class FakeTally:
    def __init__(self):
        self.submissions = []

    async def add_submission(self, data):
        self.submissions.append(data)

class SyncCourseTest(SubmissionsTestCase):
    def setUp(self):
        self.no_waiting()
        self.quizzes = AsyncMock(return_value=[{'id': '5', 'title': 'Quiz 1'}])
        self.submissions = AsyncMock(return_value={'submissions': [], 'count': 0, 'is_new_quiz': False})
        self.course_name = AsyncMock(return_value='Intro Programming')
        self.new_quiz_data = AsyncMock(return_value={'results': {}})
        self.matrices = MagicMock()
        self.matrices.update_many = AsyncMock()
        self.mastery = FakeMasteryDocs()
        self.tally = FakeTally()
        self.replace = AsyncMock()
        self.progress = MagicMock()
        self.progress.bulk_write = AsyncMock()
        db = MagicMock()
        db.get_collection.return_value = self.progress
        for target, value in [
            ('services.canvas_submissions_service.get_instructor_course_quizzes', self.quizzes),
            ('services.canvas_submissions_service.get_all_course_submissions', self.submissions),
            ('services.canvas_submissions_service.get_canvas_course_name', self.course_name),
            ('services.canvas_submissions_service.get_new_quiz_data', self.new_quiz_data),
            ('services.canvas_submissions_service.achieveup_skill_matrices_collection', self.matrices),
            ('services.canvas_submissions_service.db', db),
            ('services.mastery_service.mastery_collection', self.mastery),
            ('services.mastery_service.CourseMasteryTally', lambda: self.tally),
            ('services.mastery_service.replace_course_mastery', self.replace),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.canvas()

    async def sync(self, course_name=None):
        return await submissions_service.sync_course_submissions_direct('tok', '100', course_name)

    async def test_course_name_is_saved_on_the_course_matrices(self):
        await self.sync()
        self.course_name.assert_awaited_once_with('tok', '100')
        query, update = self.matrices.update_many.await_args.args
        self.assertEqual(query, {'course_id': {'$in': ['100', 100]}, 'course_name': {'$ne': 'Intro Programming'}})
        self.assertEqual(update, {'$set': {'course_name': 'Intro Programming'}})
        self.replace.assert_awaited_once_with('100', 'Intro Programming', self.tally)

    async def test_known_course_name_skips_the_lookup(self):
        await self.sync(course_name='Given Name')
        self.course_name.assert_not_awaited()
        self.assertEqual(self.matrices.update_many.await_args.args[1], {'$set': {'course_name': 'Given Name'}})

    async def test_unknown_course_name_leaves_matrices_alone(self):
        self.course_name.return_value = None
        await self.sync()
        self.matrices.update_many.assert_not_awaited()

    async def test_matrix_name_save_failure_does_not_stop_the_sync(self):
        self.matrices.update_many.side_effect = Exception('db down')
        with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
            result = await self.sync()
        self.assertEqual(result['message'], 'Sync completed (Direct)')

    async def test_quiz_list_error_is_returned(self):
        self.quizzes.return_value = {'error': 'Failed', 'statusCode': 401}
        self.assertEqual(await self.sync(), {'error': 'Failed', 'statusCode': 401})
        self.replace.assert_not_awaited()

    async def test_course_without_quizzes_keeps_existing_scores(self):
        self.quizzes.return_value = []
        result = await self.sync()
        self.assertEqual((result['message'], result['total_quizzes']), ('No quizzes found (Direct)', 0))
        self.replace.assert_not_awaited()

    async def test_classic_answers_are_fetched_and_tallied(self):
        self.submissions.return_value = {'is_new_quiz': False, 'submissions': [
            {'id': 10, 'user_id': 555, 'quiz_id': 5, 'user': {'name': 'Ann'}}]}
        self.fake.responses.append(FakeResponse(200, {'quiz_submission_questions': [
            {'id': 7, 'question_text': 'Q', 'correct': True}]}))
        await self.sync()
        self.assertEqual(self.fake.calls[0]['url'], f'{CANVAS_API_URL}/quiz_submissions/10/questions')
        processed = self.tally.submissions[0]
        self.assertEqual((processed['student_id'], processed['student_name'], processed['course_id'],
                          processed['course_name']), ('555', 'Ann', '100', 'Intro Programming'))
        self.assertTrue(processed['questions'][0]['correct'])

    async def test_new_quiz_answers_come_from_the_report(self):
        self.submissions.return_value = {'is_new_quiz': True, 'submissions': [
            {'id': 40, 'user_id': 555, 'quiz_id': 81}, {'id': 41, 'user_id': 556, 'quiz_id': 81}]}
        self.new_quiz_data.return_value = {'results': {'555': [{'id': 'i1', 'question_text': 'Q', 'correct': True}]}}
        await self.sync()
        self.new_quiz_data.assert_awaited_once_with('tok', '100', '5')
        by_student = {s['student_id']: s['questions'] for s in self.tally.submissions}
        self.assertEqual(len(by_student['555']), 1)
        self.assertEqual(by_student['556'], [])
        self.assertEqual(self.fake.calls, [])  # no per-submission Canvas calls

    async def test_new_quiz_report_failure_keeps_previous_scores(self):
        self.submissions.return_value = {'is_new_quiz': True, 'submissions': [{'id': 40, 'user_id': 555}]}
        self.new_quiz_data.return_value = {'error': 'New quiz report did not finish.', 'statusCode': 502}
        with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
            result = await self.sync()
        self.assertEqual((result['error'], result['total_errors'], result['statusCode']), ('Sync incomplete', 1, 502))
        self.assertEqual(self.tally.submissions, [])
        self.replace.assert_not_awaited()

    async def test_synced_count_is_submissions_cached(self):
        self.submissions.return_value = {'is_new_quiz': False, 'submissions': [
            {'id': 10, 'user_id': 555, 'questions': []}, {'id': 11, 'user_id': 556, 'questions': []}]}
        store = AsyncMock(return_value=True)
        with patch('services.canvas_submissions_service.store_submission_data', store):
            result = await self.sync()
        self.assertEqual(result['total_synced'], 2)
        self.assertEqual(store.await_count, 2)

    async def test_progress_is_rebuilt_from_mastery(self):
        self.mastery.docs = [
            {'student_id': '555', 'skill_id': 'Loops', 'total_correct': 9, 'total_attempted': 10,
             'mastery_percentage': 90.0},
            {'student_id': '555', 'skill_id': 'Recursion', 'total_correct': 1, 'total_attempted': 3,
             'mastery_percentage': 33.333},
            {'student_id': '556', 'skill_id': 'Loops', 'total_correct': 0, 'total_attempted': 2,
             'mastery_percentage': 0},
            {'skill_id': 'Loops'},  # no student, skipped
        ]
        result = await self.sync()
        self.assertEqual(result['progress_synced'], 2)
        operations = self.progress.bulk_write.await_args.args[0]
        saved = {op._filter['student_id']: op._doc['$set'] for op in operations}
        self.assertEqual(saved['555']['skill_progress'], {
            'Loops': {'score': 90.0, 'level': 'expert', 'total_questions': 10, 'correct_answers': 9, 'notes': ''},
            'Recursion': {'score': 33.3, 'level': 'beginner', 'total_questions': 3, 'correct_answers': 1,
                          'notes': ''}})
        self.assertEqual(saved['556']['skill_progress']['Loops']['level'], 'none')
        self.assertEqual(saved['555']['course_id'], '100')
        self.assertTrue(all(op._upsert for op in operations))

    async def test_no_mastery_means_no_progress_write(self):
        result = await self.sync()
        self.assertEqual(result['progress_synced'], 0)
        self.progress.bulk_write.assert_not_awaited()

    async def test_progress_failure_does_not_fail_the_sync(self):
        self.mastery.docs = [{'student_id': '555', 'skill_id': 'Loops', 'mastery_percentage': 50}]
        self.progress.bulk_write.side_effect = Exception('db down')
        with self.assertLogs('services.canvas_submissions_service', 'ERROR'):
            result = await self.sync()
        self.assertEqual((result['message'], result['progress_synced']), ('Sync completed (Direct)', 0))


if __name__ == '__main__':
    unittest.main()
