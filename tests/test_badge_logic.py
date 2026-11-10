import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from services import badge_service
from services.badge_service import CanvasVerificationUnavailable

INSTRUCTOR = {'id': 'inst-1', 'role': 'instructor', 'canvasTokenType': 'instructor', 'canvas_student_id': 900}
STUDENT = {'id': 'stu-1', 'role': 'student', 'canvasTokenType': 'student', 'canvas_student_id': 555}

class FakeSharing:
    #The badge sharing collection

    def __init__(self, docs=()):
        self.docs = [dict(doc) for doc in docs]
        self.updates = []

    def _match(self, query):
        def matches(doc, key, value):
            if isinstance(value, dict) and '$ne' in value:
                return doc.get(key) != value['$ne']
            return doc.get(key) == value
        return next((doc for doc in self.docs
                     if all(matches(doc, key, value) for key, value in query.items())), None)

    async def find_one(self, query):
        doc = self._match(query)
        return dict(doc) if doc else None

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))
        doc = self._match(query)
        if doc is None and upsert:
            doc = dict(query)
            self.docs.append(doc)
        doc.update(update.get('$set', {}))
        for key in update.get('$unset', {}):
            doc.pop(key, None)


class FakeBadges:
    def __init__(self, docs):
        self.docs = docs
        self.inserted = []

    def find(self, query, projection=None):
        def matches(doc):
            for key, value in query.items():
                if isinstance(value, dict) and '$in' in value:
                    if doc.get(key) not in value['$in']:
                        return False
                elif doc.get(key) != value:
                    return False
            return True

        async def results():
            for doc in self.docs:
                if matches(doc):
                    yield dict(doc)
        return results()

    async def insert_one(self, doc):
        self.inserted.append(doc)


def badge(badge_id, course_id='100', **overrides):
    return {'badge_id': badge_id, 'user_id': '555', 'course_id': course_id, 'skill_id': 'Loops',
            'skill_name': 'Loops', 'badge_level': 'advanced', 'badge_name': 'Advanced in Loops',
            'progress_percentage': 80.0, 'earned_at': datetime(2026, 9, 1), 'student_name': 'Ann Lee',
            'shareable_link': f'/badges/{badge_id}/share', **overrides}


def patch_all(test, targets):
    for target, value in targets:
        patcher = patch(target, value)
        patcher.start()
        test.addCleanup(patcher.stop)


class CreateBadgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mastery = MagicMock()
        self.mastery.find_one = AsyncMock(return_value={'skill_name': 'Loops', 'skill_id': 'Loops'})
        self.matrices = MagicMock()
        self.matrices.find_one = AsyncMock(return_value={'course_name': 'Intro Programming'})
        self.badges = FakeBadges([])
        patch_all(self, [
            ('services.badge_service.achieveup_student_skill_mastery_collection', self.mastery),
            ('services.badge_service.achieveup_skill_matrices_collection', self.matrices),
            ('services.badge_service.achieveup_user_badges_collection', self.badges),
        ])

    async def test_badge_document(self):
        result = await badge_service.create_badge_for_student('555', '100', 'Loops', 'advanced', 80.0, 'Ann', 'Intro')
        self.assertEqual(self.badges.inserted, [result])
        self.assertEqual(result['badge_name'], 'Advanced in Loops')
        self.assertEqual((result['course_name'], result['student_name'], result['progress_percentage']),
                         ('Intro', 'Ann', 80.0))
        self.assertEqual(result['shareable_link'], f"/badges/{result['badge_id']}/share")
        self.matrices.find_one.assert_not_awaited()

    async def test_course_name_falls_back_to_the_synced_matrix_name(self):
        result = await badge_service.create_badge_for_student('555', '100', 'Loops', 'expert', 95.0)
        self.assertEqual(result['course_name'], 'Intro Programming')
        self.matrices.find_one.assert_awaited_once_with(
            {'course_id': {'$in': ['100', 100]}, 'course_name': {'$nin': [None, '']}})

    async def test_unknown_course_name_stays_empty(self):
        self.matrices.find_one.return_value = None
        result = await badge_service.create_badge_for_student('555', '100', 'Loops', 'expert', 95.0)
        self.assertIsNone(result['course_name'])

    async def test_course_name_lookup_failure_still_awards(self):
        self.matrices.find_one.side_effect = Exception('db down')
        with self.assertLogs('services.badge_service', 'WARNING'):
            result = await badge_service.create_badge_for_student('555', '100', 'Loops', 'expert', 95.0)
        self.assertIsNone(result['course_name'])
        self.assertEqual(len(self.badges.inserted), 1)

    async def test_skill_name_falls_back_to_skill_id(self):
        self.mastery.find_one.return_value = {'skill_name': None, 'skill_id': 'Recursion'}
        result = await badge_service.create_badge_for_student('555', '100', 'Recursion', 'beginner', 30.0)
        self.assertEqual(result['badge_name'], 'Beginner in Recursion')

    async def test_save_failure_returns_none(self):
        self.badges.insert_one = AsyncMock(side_effect=Exception('db down'))
        with self.assertLogs('services.badge_service', 'ERROR'):
            self.assertIsNone(await badge_service.create_badge_for_student('555', '100', 'Loops', 'expert', 95.0))

    def test_badge_level_uses_the_skill_tiers(self):
        for percentage, level in [(100, 'expert'), (90, 'expert'), (89.9, 'advanced'), (50, 'intermediate'),
                                  (25, 'beginner'), (24.99, 'none'), (0, 'none')]:
            with self.subTest(percentage=percentage):
                self.assertEqual(badge_service.get_current_badge_level(percentage), level)


class InstructorTeachesStudentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rosters = {'100': [{'id': '1'}], '200': [{'id': '555'}], '300': [{'id': '2'}]}
        self.roster_calls = []

        async def students(canvas_token, course_id):
            self.roster_calls.append(course_id)
            return self.rosters[course_id]
        self.courses = AsyncMock(return_value=[{'id': '100'}, {'id': 200}, {'id': '300'}])
        patch_all(self, [
            ('services.achieveup_canvas_service.get_course_students', students),
            ('services.achieveup_canvas_service.get_instructor_courses', self.courses),
        ])

    async def teaches(self, student_id='555', hint=None):
        return await badge_service._instructor_teaches_student('tok', student_id, hint)

    async def test_hint_course_is_checked_first(self):
        self.assertTrue(await self.teaches(hint='200'))
        self.assertEqual(self.roster_calls, ['200'])
        self.courses.assert_not_awaited()

    async def test_wrong_hint_falls_back_to_every_course_once(self):
        self.assertTrue(await self.teaches(hint='100'))
        self.assertEqual(self.roster_calls, ['100', '200'])

    async def test_numeric_student_id_matches(self):
        self.assertTrue(await self.teaches(student_id=555))

    async def test_student_in_no_course(self):
        self.assertFalse(await self.teaches(student_id='999'))
        self.assertEqual(self.roster_calls, ['100', '200', '300'])

    async def test_failed_roster_without_a_match_is_unknown_not_no(self):
        self.rosters['300'] = {'error': 'Canvas down', 'statusCode': 502}
        with self.assertRaises(CanvasVerificationUnavailable):
            await self.teaches(student_id='999')

    async def test_match_still_counts_when_another_roster_failed(self):
        self.rosters['100'] = {'error': 'Canvas down', 'statusCode': 502}
        self.assertTrue(await self.teaches())

    async def test_failed_course_list_is_unknown(self):
        self.courses.return_value = {'error': 'Canvas down', 'statusCode': 502}
        with self.assertRaises(CanvasVerificationUnavailable):
            await self.teaches(hint='100')


class SharingTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = STUDENT
        self.sharing = FakeSharing()
        self.canvas_token = AsyncMock(return_value='canvas-token')
        self.teaches = AsyncMock(return_value=True)

        async def verify_token(token):
            return {'user': self.user}
        patch_all(self, [
            ('services.badge_service.achieveup_verify_token', verify_token),
            ('services.badge_service.achieveup_badge_sharing_collection', self.sharing),
            ('services.achieveup_auth_service.get_user_canvas_token', self.canvas_token),
            ('services.badge_service._instructor_teaches_student', self.teaches),
        ])


class ShareLinkTest(SharingTestCase):
    async def test_student_gets_their_own_link(self):
        result = await badge_service.generate_badge_share_link('t')
        self.assertEqual(result['share_link'], f"{Config.FRONTEND_URL}/badges/share/{result['share_id']}")
        self.assertEqual(self.sharing.docs[0]['canvas_student_id'], '555')
        self.assertEqual(self.sharing.docs[0]['badge_share_id'], result['share_id'])
        self.teaches.assert_not_awaited()

    async def test_existing_link_is_reused(self):
        self.sharing.docs.append({'canvas_student_id': '555', 'badge_share_id': 'share-1'})
        result = await badge_service.generate_badge_share_link('t')
        self.assertEqual(result['share_id'], 'share-1')

    async def test_own_id_sent_as_a_number_is_still_self(self):
        result = await badge_service.generate_badge_share_link('t', student_id=555)
        self.assertIn('share_link', result)
        self.teaches.assert_not_awaited()

    async def test_opted_out_student_gets_no_link(self):
        self.sharing.docs.append({'canvas_student_id': '555', 'badge_sharing_opted_out': True})
        result = await badge_service.generate_badge_share_link('t')
        self.assertEqual(result['statusCode'], 403)
        self.assertEqual(self.sharing.updates, [])

    async def test_student_cannot_share_another_student(self):
        result = await badge_service.generate_badge_share_link('t', student_id='556')
        self.assertEqual(result['statusCode'], 403)
        self.assertEqual(self.sharing.updates, [])

    async def test_instructor_shares_a_student_they_teach(self):
        self.user = INSTRUCTOR
        result = await badge_service.generate_badge_share_link('t', student_id=555, course_id='100')
        self.teaches.assert_awaited_once_with('canvas-token', '555', '100')
        self.assertEqual(self.sharing.docs[0]['canvas_student_id'], '555')
        self.assertIn('share_link', result)

    async def test_instructor_cannot_share_a_student_they_dont_teach(self):
        self.user = INSTRUCTOR
        self.teaches.return_value = False
        result = await badge_service.generate_badge_share_link('t', student_id='555')
        self.assertEqual(result['statusCode'], 403)
        self.assertEqual(self.sharing.updates, [])

    async def test_instructor_without_canvas_token(self):
        self.user = INSTRUCTOR
        self.canvas_token.return_value = None
        result = await badge_service.generate_badge_share_link('t', student_id='555')
        self.assertEqual(result['statusCode'], 403)
        self.teaches.assert_not_awaited()

    async def test_canvas_outage_is_503_not_a_denial(self):
        self.user = INSTRUCTOR
        self.teaches.side_effect = CanvasVerificationUnavailable('down')
        result = await badge_service.generate_badge_share_link('t', student_id='555')
        self.assertEqual(result['statusCode'], 503)

    async def test_instructor_respects_student_opt_out(self):
        self.user = INSTRUCTOR
        self.sharing.docs.append({'canvas_student_id': '555', 'badge_sharing_opted_out': True})
        result = await badge_service.generate_badge_share_link('t', student_id='555')
        self.assertEqual(result['statusCode'], 403)

    async def test_login_error_is_returned(self):
        with patch('services.badge_service.achieveup_verify_token',
                   AsyncMock(return_value={'error': 'Invalid token', 'statusCode': 401})):
            result = await badge_service.generate_badge_share_link('t')
        self.assertEqual(result['statusCode'], 401)


class SharingPreferenceTest(SharingTestCase):
    async def test_opting_out_kills_the_existing_link(self):
        self.sharing.docs.append({'canvas_student_id': '555', 'badge_share_id': 'share-1'})
        result = await badge_service.set_badge_sharing_opt_out('t', True)
        self.assertEqual(result, {'message': 'Badge sharing turned off', 'opted_out': True})
        self.assertNotIn('badge_share_id', self.sharing.docs[0])

    async def test_opting_back_in_needs_a_new_link(self):
        await badge_service.set_badge_sharing_opt_out('t', True)
        result = await badge_service.set_badge_sharing_opt_out('t', False)
        self.assertEqual(result['opted_out'], False)
        status = await badge_service.get_badge_share_status('t')
        self.assertEqual(status, {'shared': False, 'share_link': None, 'opted_out': False})

    async def test_status_with_an_active_link(self):
        self.sharing.docs.append({'canvas_student_id': '555', 'badge_share_id': 'share-1'})
        self.assertEqual(await badge_service.get_badge_share_status('t'), {
            'shared': True, 'share_link': f'{Config.FRONTEND_URL}/badges/share/share-1', 'opted_out': False})

    async def test_status_hides_a_leftover_link_when_opted_out(self):
        self.sharing.docs.append({'canvas_student_id': '555', 'badge_share_id': 'share-1',
                                  'badge_sharing_opted_out': True})
        self.assertEqual(await badge_service.get_badge_share_status('t'),
                         {'shared': False, 'share_link': None, 'opted_out': True})

    async def test_checking_status_never_creates_anything(self):
        await badge_service.get_badge_share_status('t')
        self.assertEqual(self.sharing.updates, [])


class PublicBadgesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sharing = FakeSharing([{'canvas_student_id': '555', 'badge_share_id': 'share-1'}])
        self.badges = FakeBadges([
            badge('b-old', earned_at=datetime(2026, 1, 1)),
            badge('b-new', course_id='200', earned_at=datetime(2026, 10, 1), course_name='Data Structures'),
            badge('b-other', earned_at=datetime(2026, 5, 1), user_id='999'),
        ])
        self.matrices = MagicMock()
        self.matrices.find_one = AsyncMock(return_value=None)
        patch_all(self, [
            ('services.badge_service.achieveup_badge_sharing_collection', self.sharing),
            ('services.badge_service.achieveup_user_badges_collection', self.badges),
            ('services.badge_service.achieveup_skill_matrices_collection', self.matrices),
        ])

    async def test_badges_for_the_link_owner_newest_first(self):
        result = await badge_service.get_public_badges_by_share('share-1')
        self.assertEqual(result['total_badges'], 2)
        self.assertEqual([b['badge_id'] for b in result['badges']], ['b-new', 'b-old'])
        self.assertEqual(result['student_name'], 'Ann Lee')

    async def test_only_public_fields_are_shown(self):
        result = await badge_service.get_public_badges_by_share('share-1')
        self.assertEqual(set(result['badges'][0]), {'badge_id', 'badge_name', 'skill_name', 'badge_level',
                                                    'progress_percentage', 'earned_at', 'course_id', 'course_name'})

    async def test_unknown_or_opted_out_link(self):
        self.sharing.docs.append({'canvas_student_id': '777', 'badge_share_id': 'share-2',
                                  'badge_sharing_opted_out': True})
        for share_id in ('nope', 'share-2'):
            with self.subTest(share_id=share_id):
                result = await badge_service.get_public_badges_by_share(share_id)
                self.assertEqual(result['statusCode'], 404)

    async def test_old_placeholder_names_are_repaired(self):
        self.badges.docs = [badge('b-1', skill_name='Skill', skill_id='Recursion', badge_name='Skill',
                                  badge_level='expert', course_name='Unknown Course')]
        shown = (await badge_service.get_public_badges_by_share('share-1'))['badges'][0]
        self.assertEqual((shown['skill_name'], shown['badge_name'], shown['course_name']),
                         ('Recursion', 'Expert in Recursion', None))

    async def test_name_comes_from_the_course_instructors_roster(self):
        self.badges.docs = [badge('b-1', student_name=None)]
        self.matrices.find_one.return_value = {'created_by': 'inst-1'}
        roster = AsyncMock(return_value=[{'id': '555', 'name': 'Ann From Canvas'}])
        token = AsyncMock(return_value='canvas-token')
        with patch('services.achieveup_auth_service.get_user_canvas_token', token), \
             patch('services.achieveup_canvas_service.get_course_students', roster):
            result = await badge_service.get_public_badges_by_share('share-1')
        self.assertEqual(result['student_name'], 'Ann From Canvas')
        token.assert_awaited_once_with('inst-1')
        roster.assert_awaited_once_with('canvas-token', '100')

    async def test_name_is_unknown_rather_than_guessed(self):
        self.badges.docs = [badge('b-1', student_name=None)]
        for matrix, canvas_token, roster in [
            (None, 'canvas-token', [{'id': '555', 'name': 'X'}]),             #no course owner
            ({'created_by': 'inst-1'}, None, [{'id': '555', 'name': 'X'}]),   #owner has no token
            ({'created_by': 'inst-1'}, 'canvas-token', {'error': 'down'}),    #roster failed
            ({'created_by': 'inst-1'}, 'canvas-token', [{'id': '1', 'name': 'X'}]),  #not on roster
        ]:
            with self.subTest(matrix=matrix, canvas_token=canvas_token, roster=roster), \
                 patch('services.achieveup_auth_service.get_user_canvas_token', AsyncMock(return_value=canvas_token)), \
                 patch('services.achieveup_canvas_service.get_course_students', AsyncMock(return_value=roster)):
                self.matrices.find_one.return_value = matrix
                result = await badge_service.get_public_badges_by_share('share-1')
                self.assertIsNone(result['student_name'])

    async def test_no_badges_yet(self):
        self.badges.docs = []
        result = await badge_service.get_public_badges_by_share('share-1')
        self.assertEqual(result, {'student_name': None, 'total_badges': 0, 'badges': []})


class EarnedBadgesDetailTest(unittest.IsolatedAsyncioTestCase):
    """A student viewing their own badges; access rules are in test_student_data_access."""

    def setUp(self):
        self.badges = FakeBadges([
            badge('b-100', earned_at=datetime(2026, 9, 1)),
            badge('b-200', course_id='200', earned_at=datetime(2026, 10, 1)),
            badge('b-300', course_id='300', earned_at=datetime(2026, 8, 1), course_name='Stored Name'),
        ])
        matrix_names = {'100': 'Intro Programming'}

        async def find_matrix(query, projection=None):
            course_id = query['course_id']['$in'][0]
            return {'course_name': matrix_names[course_id]} if course_id in matrix_names else None
        matrices = MagicMock()
        matrices.find_one = AsyncMock(side_effect=find_matrix)
        self.canvas_courses = AsyncMock(return_value=[{'id': '200', 'name': 'Nick', 'original_name': 'Data Structures'}])
        patch_all(self, [
            ('services.badge_service.achieveup_verify_token', AsyncMock(return_value={'user': STUDENT})),
            ('services.badge_service.achieveup_user_badges_collection', self.badges),
            ('services.badge_service.achieveup_skill_matrices_collection', matrices),
            ('services.achieveup_auth_service.get_user_canvas_token', AsyncMock(return_value='canvas-token')),
            ('services.achieveup_canvas_service.get_instructor_courses', self.canvas_courses),
        ])

    async def view(self):
        return await badge_service.get_student_earned_badges('t', '555')

    async def test_course_names_from_matrix_then_canvas_then_stored(self):
        result = await self.view()
        names = {b['badge_id']: b['course_name'] for b in result['badges']}
        self.assertEqual(names, {'b-100': 'Intro Programming', 'b-200': 'Data Structures', 'b-300': 'Stored Name'})

    async def test_canvas_is_only_asked_for_names_still_missing(self):
        self.badges.docs = [badge('b-100')]
        await self.view()
        self.canvas_courses.assert_not_awaited()

    async def test_canvas_failure_falls_back_to_stored_names(self):
        self.canvas_courses.return_value = {'error': 'down', 'statusCode': 502}
        result = await self.view()
        names = {b['badge_id']: b['course_name'] for b in result['badges']}
        self.assertEqual(names['b-200'], None)
        self.assertEqual(names['b-300'], 'Stored Name')

    async def test_newest_first_with_totals(self):
        result = await self.view()
        self.assertEqual([b['badge_id'] for b in result['badges']], ['b-200', 'b-100', 'b-300'])
        self.assertEqual((result['student_id'], result['student_name'], result['total_badges']),
                         ('555', 'Ann Lee', 3))

    async def test_placeholder_badge_names_are_repaired(self):
        self.badges.docs = [badge('b-1', badge_name='Expert in Skill', skill_name=None, skill_id='Graphs',
                                  badge_level='expert')]
        shown = (await self.view())['badges'][0]
        self.assertEqual((shown['badge_name'], shown['skill_name']), ('Expert in Graphs', 'Graphs'))

    async def test_internal_fields_are_left_out(self):
        shown = (await self.view())['badges'][0]
        self.assertNotIn('user_id', shown)
        self.assertNotIn('shareable_link', shown)
        self.assertNotIn('student_name', shown)

if __name__ == '__main__':
    unittest.main()
