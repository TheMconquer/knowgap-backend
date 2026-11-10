import logging
import unittest
from unittest.mock import AsyncMock, patch

from app import app


def setUpModule():
    #These tests call rate limited routes more often than the limits allow
    limiter_off = patch.dict(app.config, {'QUART_RATE_LIMITER_ENABLED': False})
    limiter_off.start()
    unittest.addModuleCleanup(limiter_off.stop)

AUTH = {'Authorization': 'Bearer t'}
ROUTES_MODULE = 'routes.achieveup_routes'

#Routes that needs a login
PROTECTED = [
    ('post', '/achieveup/matrix/create', {'course_id': '100', 'matrix_name': 'M'}, f'{ROUTES_MODULE}.create_skill_matrix'),
    ('put', '/achieveup/matrix/m-1', {'skills': ['A']}, f'{ROUTES_MODULE}.update_skill_matrix'),
    ('get', '/achieveup/matrix/course/100', None, f'{ROUTES_MODULE}.get_all_skill_matrices_by_course'),
    ('delete', '/achieveup/matrix/delete/m-1', None, f'{ROUTES_MODULE}.delete_skill_matrix'),
    ('get', '/achieveup/course-description/100', None, f'{ROUTES_MODULE}.get_course_description'),
    ('put', '/achieveup/course-description/100', {'description': 'x'}, f'{ROUTES_MODULE}.upsert_course_description'),
    ('post', '/achieveup/skills/assign', {'course_id': '100', 'question_skills': {'k': ['A']}},
     f'{ROUTES_MODULE}.assign_skills_to_questions'),
    ('get', '/achieveup/skills/assignments?course_id=100&question_id=k', None, f'{ROUTES_MODULE}.get_assigned_skills'),
    ('get', '/achieveup/badges/student/555/earned', None, 'services.badge_service.get_student_earned_badges'),
    ('post', '/achieveup/badges/share-profile', {}, 'services.badge_service.generate_badge_share_link'),
    ('post', '/achieveup/badges/opt-out', None, 'services.badge_service.set_badge_sharing_opt_out'),
    ('post', '/achieveup/badges/opt-in', None, 'services.badge_service.set_badge_sharing_opt_out'),
    ('get', '/achieveup/badges/share-status', None, 'services.badge_service.get_badge_share_status'),
    ('get', '/achieveup/progress/555/100', None, f'{ROUTES_MODULE}.get_student_progress'),
    ('get', '/achieveup/instructor/courses/100/channels', None, f'{ROUTES_MODULE}.get_course_channels'),
    ('put', '/achieveup/instructor/courses/100/channels', {'channels': []}, f'{ROUTES_MODULE}.update_course_channels'),
    ('post', '/achieveup/instructor/course/100/force-sync', None, 'services.achieveup_service.require_course_instructor'),
    ('post', '/achieveup/ai/suggest-skills', {'courseId': '100', 'courseName': 'N', 'courseCode': 'C'},
     'services.achieveup_service.suggest_course_skills_ai'),
    ('post', '/achieveup/ai/analyze-questions', {'courseId': '100', 'questions': [{'id': 1}]},
     'services.achieveup_service.analyze_questions_with_ai'),
    ('get', '/achieveup/instructor/courses/100/student-analytics', None,
     'services.analytics_service.get_course_students_analytics'),
    ('post', '/achieveup/matrix/import', {'source_course_id': '50', 'target_course_id': '100'},
     'services.achieveup_auth_service.achieveup_verify_token'),
    ('get', '/achieveup/import-status/100', None, 'services.achieveup_service.get_import_status'),
    ('get', '/achieveup/instructor/course/100/sync-status', None, 'services.achieveup_service.require_course_instructor'),
]

class RouteTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        quiet = patch.object(logging.getLogger('routes.achieveup_routes'), 'disabled', True)
        quiet.start()
        self.addCleanup(quiet.stop)

    async def send(self, method, path, body=None, headers=AUTH, **kwargs):
        client = app.test_client()
        if body is not None:
            kwargs['json'] = body
        return await getattr(client, method)(path, headers=headers, **kwargs)

    def service(self, target, result=None):
        mock = AsyncMock(return_value={'ok': True} if result is None else result)
        patcher = patch(target, mock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

class LoginRequiredTest(RouteTestCase):
    async def test_routes_need_a_bearer_token(self):
        for method, path, body, target in PROTECTED:
            for headers in ({}, {'Authorization': 't'}, {'Authorization': 'Basic dDp0'}):
                with self.subTest(path=path, method=method, headers=headers), patch(target, AsyncMock()) as service:
                    response = await self.send(method, path, body, headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual((await response.get_json())['error'], 'Missing token')
                    service.assert_not_called()

    async def test_public_badge_page_needs_no_login(self):
        service = self.service('services.badge_service.get_public_badges_by_share', {'badges': []})
        response = await self.send('get', '/achieveup/public/badges/share/share-1/earned', headers={})
        self.assertEqual(response.status_code, 200)
        service.assert_awaited_once_with('share-1')

class RequestBodyTest(RouteTestCase):
    async def test_body_is_required(self):
        for method, path, target in [
            ('post', '/achieveup/matrix/create', f'{ROUTES_MODULE}.create_skill_matrix'),
            ('put', '/achieveup/matrix/m-1', f'{ROUTES_MODULE}.update_skill_matrix'),
            ('post', '/achieveup/skills/assign', f'{ROUTES_MODULE}.assign_skills_to_questions'),
            ('post', '/achieveup/ai/suggest-skills', 'services.achieveup_service.suggest_course_skills_ai'),
            ('post', '/achieveup/ai/analyze-questions', 'services.achieveup_service.analyze_questions_with_ai'),
        ]:
            with self.subTest(path=path), patch(target, AsyncMock()) as service:
                response = await self.send(method, path, {})
                self.assertEqual(response.status_code, 400)
                self.assertEqual((await response.get_json())['message'], 'Request body is required')
                service.assert_not_called()

    async def test_import_needs_a_body(self):
        self.service('services.achieveup_auth_service.achieveup_verify_token', {'user': {'id': 'inst-1'}})
        response = await self.send('post', '/achieveup/matrix/import', {})
        self.assertEqual(response.status_code, 400)

    async def test_required_fields(self):
        for method, path, body, message, target in [
            ('post', '/achieveup/matrix/create', {'course_id': '100'},
             'Course ID and matrix name are required', f'{ROUTES_MODULE}.create_skill_matrix'),
            ('post', '/achieveup/matrix/create', {'matrix_name': 'M'},
             'Course ID and matrix name are required', f'{ROUTES_MODULE}.create_skill_matrix'),
            ('post', '/achieveup/skills/assign', {'course_id': '100', 'question_skills': {}},
             'Course ID and question skills are required', f'{ROUTES_MODULE}.assign_skills_to_questions'),
            ('post', '/achieveup/skills/assign', {'question_skills': {'k': ['A']}},
             'Course ID and question skills are required', f'{ROUTES_MODULE}.assign_skills_to_questions'),
        ]:
            with self.subTest(path=path, body=body), patch(target, AsyncMock()) as service:
                response = await self.send(method, path, body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual((await response.get_json())['message'], message)
                service.assert_not_called()

    async def test_assignment_lookup_needs_course_and_questions(self):
        service = self.service(f'{ROUTES_MODULE}.get_assigned_skills')
        for query in ('', '?course_id=100', '?question_id=k'):
            with self.subTest(query=query):
                response = await self.send('get', f'/achieveup/skills/assignments{query}')
                self.assertEqual(response.status_code, 400)
        service.assert_not_called()

    async def test_course_description_body_must_be_an_object(self):
        service = self.service(f'{ROUTES_MODULE}.upsert_course_description')
        for body in (['x'], 'text', 5):
            with self.subTest(body=body):
                response = await self.send('put', '/achieveup/course-description/100', body)
                self.assertEqual(response.status_code, 400)
        service.assert_not_called()

    async def test_missing_description_is_saved_as_empty(self):
        service = self.service(f'{ROUTES_MODULE}.upsert_course_description', {'description': ''})
        await self.send('put', '/achieveup/course-description/100', {'other': 1})
        service.assert_awaited_once_with('t', '100', '')

    async def test_channel_body_checks(self):
        service = self.service(f'{ROUTES_MODULE}.update_course_channels', {'channels': []})
        for kwargs, message in [
            ({'data': 'not json', 'headers': {**AUTH, 'Content-Type': 'application/json'}},
             'Request body must be valid JSON.'),
            ({'json': {'other': []}}, "Missing 'channels' array in request body."),
            ({'json': ['@a']}, "Missing 'channels' array in request body."),
            ({'json': {'channels': '@a'}}, "'channels' must be a list of channel handles or IDs."),
        ]:
            with self.subTest(kwargs=kwargs):
                response = await app.test_client().put('/achieveup/instructor/courses/100/channels',
                                                       **{'headers': AUTH, **kwargs})
                self.assertEqual(response.status_code, 400)
                self.assertEqual((await response.get_json())['message'], message)
        service.assert_not_called()

    async def test_suggestions_list_every_missing_field(self):
        service = self.service('services.achieveup_service.suggest_course_skills_ai')
        response = await self.send('post', '/achieveup/ai/suggest-skills', {'courseDescription': 'About'})
        body = await response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body['message'], 'courseId is required; courseName is required; courseCode is required')
        self.assertEqual(body['received_data']['description_length'], 5)
        service.assert_not_called()

    async def test_analysis_needs_questions(self):
        service = self.service('services.achieveup_service.analyze_questions_with_ai')
        for body in ({'courseId': '100'}, {'courseId': '100', 'questions': []}):
            with self.subTest(body=body):
                response = await self.send('post', '/achieveup/ai/analyze-questions', body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual((await response.get_json())['message'], 'questions array is required')
        service.assert_not_called()

    async def test_import_needs_two_different_courses(self):
        self.service('services.achieveup_auth_service.achieveup_verify_token', {'user': {'id': 'inst-1'}})
        importers = {'/achieveup/matrix/import': 'services.achieveup_service.import_matrices_from_course'}
        for path, target in importers.items():
            for body, message in [
                ({'source_course_id': '50'}, 'source_course_id and target_course_id are required'),
                ({'target_course_id': '100'}, 'source_course_id and target_course_id are required'),
                ({'source_course_id': '100', 'target_course_id': '100'}, 'Source and target course cannot be the same'),
            ]:
                with self.subTest(path=path, body=body), patch(target, AsyncMock()) as service:
                    response = await self.send('post', path, body)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual((await response.get_json())['message'], message)
                    service.assert_not_called()

    async def test_import_login_error_is_returned(self):
        self.service('services.achieveup_auth_service.achieveup_verify_token',
                     {'error': 'Token expired', 'statusCode': 401})
        response = await self.send('post', '/achieveup/matrix/import', {'source_course_id': '50', 'target_course_id': '100'})
        self.assertEqual(response.status_code, 401)


class ServiceCallTest(RouteTestCase):
    async def test_values_reach_the_service(self):
        for method, path, body, target, args, kwargs in [
            ('post', '/achieveup/matrix/create', {'course_id': '100', 'matrix_name': 'M', 'skills': ['A']},
             f'{ROUTES_MODULE}.create_skill_matrix', ('t', '100', 'M', ['A']), {}),
            ('post', '/achieveup/matrix/create', {'course_id': '100', 'matrix_name': 'M'},
             f'{ROUTES_MODULE}.create_skill_matrix', ('t', '100', 'M', []), {}),
            ('put', '/achieveup/matrix/m-1', {'skills': ['A'], 'matrix_name': 'Renamed'},
             f'{ROUTES_MODULE}.update_skill_matrix', ('t', 'm-1', ['A'], 'Renamed'), {}),
            ('get', '/achieveup/skills/assignments?course_id=100&question_id=k1&question_id=k2', None,
             f'{ROUTES_MODULE}.get_assigned_skills', ('t', '100', ['k1', 'k2']), {}),
            ('get', '/achieveup/badges/student/555/earned?course_id=100', None,
             'services.badge_service.get_student_earned_badges', ('t', '555', '100'), {}),
            ('get', '/achieveup/badges/student/555/earned', None,
             'services.badge_service.get_student_earned_badges', ('t', '555', None), {}),
            ('post', '/achieveup/badges/share-profile', {'student_id': '555', 'course_id': '100'},
             'services.badge_service.generate_badge_share_link', ('t', '555', '100'), {}),
            ('post', '/achieveup/badges/opt-out', None,
             'services.badge_service.set_badge_sharing_opt_out', ('t', True), {}),
            ('post', '/achieveup/badges/opt-in', None,
             'services.badge_service.set_badge_sharing_opt_out', ('t', False), {}),
            ('get', '/achieveup/progress/555/100', None, f'{ROUTES_MODULE}.get_student_progress', ('t', '555', '100'), {}),
            ('put', '/achieveup/instructor/courses/100/channels', {'channels': ['@a']},
             f'{ROUTES_MODULE}.update_course_channels', ('t', '100', ['@a']), {}),
            ('post', '/achieveup/ai/analyze-questions', {'courseId': '100', 'matrixId': 'm-1', 'questions': [{'id': 1}]},
             'services.achieveup_service.analyze_questions_with_ai', ('t', [{'id': 1}], '100', 'm-1'), {}),
            ('post', '/achieveup/ai/analyze-questions', {'courseId': '100', 'matrix_id': 'm-2', 'questions': [{'id': 1}]},
             'services.achieveup_service.analyze_questions_with_ai', ('t', [{'id': 1}], '100', 'm-2'), {}),
            ('get', '/achieveup/import-status/100', None, 'services.achieveup_service.get_import_status', ('t', '100'), {}),
        ]:
            result = {'channels': []} if 'channels' in path else None
            with self.subTest(method=method, path=path, body=body), \
                 patch(target, AsyncMock(return_value=result or {'ok': True})) as service:
                await self.send(method, path, body)
                self.assertEqual(service.await_args.args, args)
                self.assertEqual(service.await_args.kwargs, kwargs)

    async def test_share_link_without_a_body_is_for_the_caller(self):
        service = self.service('services.badge_service.generate_badge_share_link')
        response = await app.test_client().post('/achieveup/badges/share-profile', headers=AUTH)
        self.assertEqual(response.status_code, 200)
        service.assert_awaited_once_with('t', None, None)

    async def test_suggestions_receive_the_course(self):
        service = self.service('services.achieveup_service.suggest_course_skills_ai', {'skills': []})
        await self.send('post', '/achieveup/ai/suggest-skills',
                        {'courseId': '100', 'courseName': 'Intro', 'courseCode': 'COP1000', 'extra': 'x'})
        service.assert_awaited_once_with('t', {'courseId': '100', 'courseName': 'Intro', 'courseCode': 'COP1000',
                                               'courseDescription': ''})

    async def test_import_uses_the_logged_in_user(self):
        self.service('services.achieveup_auth_service.achieveup_verify_token', {'user': {'id': 'inst-1'}})
        body = {'source_course_id': '50', 'target_course_id': '100'}
        with patch('services.achieveup_service.import_matrices_from_course', AsyncMock(return_value=[])) as service:
            await self.send('post', '/achieveup/matrix/import', body)
        service.assert_awaited_once_with(source_course_id='50', target_course_id='100', user_id='inst-1', token='t')


class ResponseShapeTest(RouteTestCase):
    async def test_matrix_created(self):
        self.service(f'{ROUTES_MODULE}.create_skill_matrix', {'_id': 'm-1', 'matrix_name': 'M'})
        response = await self.send('post', '/achieveup/matrix/create', {'course_id': '100', 'matrix_name': 'M'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(await response.get_json(), {'_id': 'm-1', 'matrix_name': 'M'})

    async def test_course_matrices_are_a_plain_list(self):
        self.service(f'{ROUTES_MODULE}.get_all_skill_matrices_by_course', {'matrices': [{'_id': 'm-1'}], 'count': 1})
        response = await self.send('get', '/achieveup/matrix/course/100')
        self.assertEqual(await response.get_json(), [{'_id': 'm-1'}])

    async def test_matrix_deleted(self):
        self.service(f'{ROUTES_MODULE}.delete_skill_matrix', {'success': True})
        response = await self.send('delete', '/achieveup/matrix/delete/m-1')
        self.assertEqual(await response.get_json(), {'message': 'Skill matrix deleted successfully', 'matrix_id': 'm-1'})

    async def test_skills_assigned(self):
        self.service(f'{ROUTES_MODULE}.assign_skills_to_questions', {'message': 'anything'})
        response = await self.send('post', '/achieveup/skills/assign', {'course_id': '100', 'question_skills': {'k': ['A']}})
        self.assertEqual(await response.get_json(), {'message': 'Skills assigned successfully'})

    async def test_channels(self):
        self.service(f'{ROUTES_MODULE}.get_course_channels', [{'channel_id': 'UC1', 'name': 'A'}])
        response = await self.send('get', '/achieveup/instructor/courses/100/channels')
        self.assertEqual(await response.get_json(), {'success': True, 'course_id': '100',
                                                     'channels': [{'channel_id': 'UC1', 'name': 'A'}]})
        self.service(f'{ROUTES_MODULE}.update_course_channels', {'channels': [{'channel_id': 'UC2', 'name': 'B'}]})
        response = await self.send('put', '/achieveup/instructor/courses/100/channels', {'channels': ['@b']})
        self.assertEqual((await response.get_json())['channels'], [{'channel_id': 'UC2', 'name': 'B'}])

    async def test_suggestions_return_just_the_skills(self):
        self.service('services.achieveup_service.suggest_course_skills_ai',
                     {'skills': [{'skill': 'Loops'}], 'method': 'ai'})
        response = await self.send('post', '/achieveup/ai/suggest-skills',
                                   {'courseId': '100', 'courseName': 'N', 'courseCode': 'C'})
        self.assertEqual(await response.get_json(), [{'skill': 'Loops'}])

    async def test_analysis_returns_just_the_analyses(self):
        self.service('services.achieveup_service.analyze_questions_with_ai',
                     {'analyses': [{'questionId': 1, 'suggestedSkills': ['A']}], 'skill_count': 1})
        response = await self.send('post', '/achieveup/ai/analyze-questions', {'courseId': '100', 'questions': [{'id': 1}]})
        self.assertEqual(await response.get_json(), [{'questionId': 1, 'suggestedSkills': ['A']}])

    async def test_analytics_return_the_analytics_object(self):
        self.service('services.analytics_service.get_course_students_analytics',
                     {'analytics': {'students': [], 'skillDistribution': {}, 'averageScores': {}}})
        response = await self.send('get', '/achieveup/instructor/courses/100/student-analytics')
        self.assertEqual(await response.get_json(), {'students': [], 'skillDistribution': {}, 'averageScores': {}})

    async def test_import_reports_the_count(self):
        self.service('services.achieveup_auth_service.achieveup_verify_token', {'user': {'id': 'inst-1'}})
        self.service('services.achieveup_service.import_matrices_from_course', [{'_id': 'a'}, {'_id': 'b'}])
        response = await self.send('post', '/achieveup/matrix/import', {'source_course_id': '50', 'target_course_id': '100'})
        self.assertEqual(await response.get_json(), {'message': 'Matrices imported successfully', 'imported_count': 2,
                                                     'matrices': [{'_id': 'a'}, {'_id': 'b'}]})

    async def test_force_sync_needs_a_canvas_token(self):
        self.service('services.achieveup_service.require_course_instructor', ({'id': 'inst-1'}, None))
        no_token = patch('services.achieveup_auth_service.get_user_canvas_token', AsyncMock(return_value=None))
        no_token.start()
        self.addCleanup(no_token.stop)
        claim = self.service('services.sync_status_service.try_start_sync')
        response = await self.send('post', '/achieveup/instructor/course/100/force-sync')
        self.assertEqual(response.status_code, 400)
        claim.assert_not_called()  # the course isnt marked as syncing


class ServiceErrorTest(RouteTestCase):
    async def test_service_errors_keep_their_status(self):
        error = {'error': 'Access denied', 'message': 'Not your course', 'statusCode': 403}
        for method, path, body, target in PROTECTED:
            if target.endswith(('achieveup_verify_token', 'require_course_instructor')):
                continue
            with self.subTest(path=path, method=method), patch(target, AsyncMock(return_value=error)):
                response = await self.send(method, path, body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual((await response.get_json())['error'], 'Access denied')

    async def test_import_error_keeps_its_status(self):
        self.service('services.achieveup_auth_service.achieveup_verify_token', {'user': {'id': 'inst-1'}})
        error = {'error': 'Access denied', 'statusCode': 403}
        with patch('services.achieveup_service.import_matrices_from_course', AsyncMock(return_value=error)):
            response = await self.send('post', '/achieveup/matrix/import',
                                       {'source_course_id': '50', 'target_course_id': '100'})
        self.assertEqual(response.status_code, 403)

    async def test_public_badge_link_not_found(self):
        self.service('services.badge_service.get_public_badges_by_share',
                     {'error': 'Not found', 'message': 'This share link is invalid or no longer active', 'statusCode': 404})
        response = await self.send('get', '/achieveup/public/badges/share/nope/earned', headers={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual((await response.get_json())['message'], 'This share link is invalid or no longer active')

    async def test_unexpected_errors_are_a_generic_500(self):
        secret = 'secret internal detail'
        for method, path, body, target in PROTECTED + [
                ('get', '/achieveup/public/badges/share/s/earned', None,
                 'services.badge_service.get_public_badges_by_share')]:
            if target.endswith(('achieveup_verify_token', 'get_import_status')):
                continue  # the import routes are covered in test_error_messages
            with self.subTest(path=path, method=method), \
                 patch(target, AsyncMock(side_effect=Exception(secret))), \
                 patch('traceback.print_exc'):
                response = await self.send(method, path, body)
                self.assertEqual(response.status_code, 500)
                body_json = await response.get_json()
                self.assertEqual(body_json['error'], 'Internal server error')
                self.assertNotIn(secret, str(body_json))


if __name__ == '__main__':
    unittest.main()
