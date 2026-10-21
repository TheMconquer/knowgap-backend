import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import achieveup_canvas_service as canvas_service
from services import achieveup_service

INSTRUCTOR = {'id': 'inst-1', 'role': 'instructor', 'canvasTokenType': 'instructor'}


def students(*ids):
    return [{'id': student_id} for student_id in ids]


class InstructorDashboardStudentCountTest(unittest.IsolatedAsyncioTestCase):
    async def dashboard(self, courses, rosters):
        """Run the dashboard with Canvas returning `courses` and each course's roster from `rosters`."""
        self.get_courses = AsyncMock(return_value=courses)

        async def get_course_students(canvas_token, course_id):
            return rosters[course_id]

        # Stubbed so the test never touches a real database.
        matrices = MagicMock()
        matrices.count_documents = AsyncMock(return_value=0)
        matrices.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        with patch('services.achieveup_service.achieveup_verify_token',
                   AsyncMock(return_value={'user': INSTRUCTOR})), \
             patch('services.achieveup_auth_service.get_user_canvas_token',
                   AsyncMock(return_value='canvas-token')), \
             patch('services.achieveup_canvas_service.get_instructor_courses', self.get_courses), \
             patch('services.achieveup_canvas_service.get_course_students', get_course_students), \
             patch('services.achieveup_service.achieveup_skill_matrices_collection', matrices):
            return await achieveup_service.get_instructor_dashboard('t')

    async def test_counts_students_across_all_courses(self):
        result = await self.dashboard(
            [{'id': '100'}, {'id': '200'}],
            {'100': students('1', '2'), '200': students('3')})
        self.assertEqual(result['students'], 3)

    async def test_student_in_several_courses_counted_once(self):
        result = await self.dashboard(
            [{'id': '100'}, {'id': '200'}],
            {'100': students('1', '2'), '200': students('2', '3')})
        self.assertEqual(result['students'], 3)

    async def test_count_is_unknown_if_any_roster_fails(self):
        result = await self.dashboard(
            [{'id': '100'}, {'id': '200'}, {'id': '300'}],
            {'100': students('1'), '200': {'error': 'Canvas error', 'statusCode': 502},
             '300': students('2')})
        self.assertIsNone(result['students'])
        self.assertEqual(result['totalCourses'], 3)

    async def test_no_active_courses(self):
        result = await self.dashboard([], {})
        self.assertEqual(result['students'], 0)

    async def test_only_active_courses_are_requested(self):
        await self.dashboard([], {})
        self.get_courses.assert_awaited_once_with('canvas-token', active_only=True)

    async def test_course_list_error_is_returned(self):
        error = {'error': 'Failed to fetch instructor courses', 'statusCode': 502}
        result = await self.dashboard(error, {})
        self.assertEqual(result, error)


class FakeResponse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    status = 200
    headers = {}

    async def json(self):
        return []


class FakeSession:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        self.calls.append(params)
        return FakeResponse()


class InstructorCoursesActiveOnlyTest(unittest.IsolatedAsyncioTestCase):
    async def params_sent(self, **kwargs):
        calls = []
        with patch('services.achieveup_canvas_service.create_canvas_session',
                   side_effect=lambda: FakeSession(calls)):
            await canvas_service.get_instructor_courses('tok', **kwargs)
        return calls[0]

    async def test_default_includes_past_courses(self):
        self.assertEqual(await self.params_sent(), {'enrollment_type': 'teacher', 'per_page': 100})

    async def test_active_only_asks_canvas_for_active_enrollments(self):
        self.assertEqual(await self.params_sent(active_only=True),
                         {'enrollment_type': 'teacher', 'per_page': 100, 'enrollment_state': 'active'})


if __name__ == '__main__':
    unittest.main()
