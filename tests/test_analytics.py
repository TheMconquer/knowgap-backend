import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from services import analytics_service

INSTRUCTOR = {'id': 'inst-1', 'role': 'instructor', 'canvasTokenType': 'instructor'}


def matches(doc, query):
    for key, value in query.items():
        if isinstance(value, dict) and '$in' in value:
            if doc.get(key) not in value['$in']:
                return False
        elif isinstance(doc.get(key), list):
            if value not in doc[key]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self.docs]

    def __aiter__(self):
        async def results():
            for doc in self.docs:
                yield dict(doc)
        return results()


class FakeCollection:
    def __init__(self, docs=()):
        self.docs = list(docs)

    def find(self, query, projection=None):
        return FakeCursor([doc for doc in self.docs if matches(doc, query)])

    async def distinct(self, field, query):
        values = []
        for doc in self.docs:
            if matches(doc, query) and doc.get(field) not in values:
                values.append(doc.get(field))
        return values

    async def count_documents(self, query):
        return len([doc for doc in self.docs if matches(doc, query)])


class FakeDb:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def mastery(student_id, skill_id, percentage, correct=0, attempted=0):
    return {'student_id': student_id, 'course_id': '100', 'skill_id': skill_id,
            'mastery_percentage': percentage, 'total_correct': correct, 'total_attempted': attempted}


class CourseAnalyticsTest(unittest.IsolatedAsyncioTestCase):
    """Course '100' has two students; access checks are in test_student_data_access."""

    def setUp(self):
        self.db = FakeDb()
        self.question_skills = self.db[Config.ACHIEVEUP_QUESTION_SKILLS_COLLECTION]
        self.question_skills.docs = [
            {'question_id': 'q1', 'course_ids': ['100'], 'skills': ['Loops']},
            {'question_id': 'q2', 'course_ids': ['100', '50'], 'skills': ['Recursion', 'Loops']},
            {'question_id': 'q3', 'course_ids': ['999'], 'skills': ['Other Course Skill']},
        ]
        self.mastery = self.db[Config.ACHIEVEUP_STUDENT_SKILL_MASTERY_COLLECTION]
        self.badges = FakeCollection()
        self.roster = AsyncMock(return_value=[{'id': '555', 'name': 'Ann'}, {'id': '556'}])
        for target, value in [
            ('services.analytics_service.db', self.db),
            ('services.analytics_service.achieveup_user_badges_collection', self.badges),
            ('services.achieveup_service.require_course_instructor', AsyncMock(return_value=(INSTRUCTOR, None))),
            ('services.achieveup_service.get_instructor_course_students', self.roster),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def analytics(self):
        result = await analytics_service.get_course_students_analytics('t', '100')
        return result['analytics']

    async def student(self, student_id):
        return next(s for s in (await self.analytics())['students'] if s['id'] == student_id)

    async def test_skill_breakdown(self):
        self.mastery.docs = [mastery('555', 'Loops', 82.456, correct=9, attempted=11)]
        ann = await self.student('555')
        self.assertEqual(ann['skillBreakdown']['Loops'],
                         {'score': 82.5, 'level': 'advanced', 'questionsAttempted': 11, 'questionsCorrect': 9})
        self.assertEqual(ann['skillBreakdown']['Recursion'],
                         {'score': 0, 'level': 'none', 'questionsAttempted': 0, 'questionsCorrect': 0})
        self.assertEqual(ann['name'], 'Ann')

    async def test_student_without_name(self):
        self.assertEqual((await self.student('556'))['name'], 'Unknown')

    async def test_only_this_courses_skills_are_listed(self):
        ann = await self.student('555')
        self.assertEqual(set(ann['skillBreakdown']), {'Loops', 'Recursion'})

    async def test_skills_only_in_older_mastery_records_are_included(self):
        self.mastery.docs = [mastery('555', 'Legacy Skill', 60)]
        ann = await self.student('555')
        self.assertEqual(ann['skillBreakdown']['Legacy Skill']['score'], 60)

    async def test_skill_objects_from_older_mappings_use_their_name(self):
        self.question_skills.docs = [{'course_ids': ['100'], 'skills': [{'id': 's1', 'name': 'Graphs'}]}]
        self.mastery.docs = [mastery('555', 'Graphs', 95)]
        ann = await self.student('555')
        self.assertEqual(ann['skillBreakdown']['Graphs']['level'], 'expert')

    async def test_mastered_means_intermediate_or_better(self):
        self.mastery.docs = [mastery('555', 'Loops', 50), mastery('555', 'Recursion', 49.9)]
        self.assertEqual((await self.student('555'))['skillsMastered'], 1)

    async def test_progress_and_risk_level(self):
        for scores, progress, risk in [
            ((100, 50), 75, 'low'),
            ((74, 75.8), 74.9, 'medium'),
            ((50, 50), 50, 'medium'),
            ((49, 50), 49.5, 'high'),
            ((0, 0), 0, 'high'),
        ]:
            with self.subTest(scores=scores):
                self.mastery.docs = [mastery('555', 'Loops', scores[0]), mastery('555', 'Recursion', scores[1])]
                ann = await self.student('555')
                self.assertEqual((ann['progress'], ann['riskLevel']), (progress, risk))

    async def test_highest_badge_per_skill(self):
        self.badges.docs = [
            {'user_id': '555', 'course_id': '100', 'skill_id': 'Loops', 'badge_level': 'beginner',
             'earned_at': datetime(2026, 9, 1)},
            {'user_id': '555', 'course_id': '100', 'skill_id': 'Loops', 'badge_level': 'advanced',
             'earned_at': datetime(2026, 10, 1)},
            {'user_id': '555', 'course_id': '100', 'skill_id': 'Loops', 'badge_level': 'intermediate',
             'earned_at': datetime(2026, 9, 15)},
            {'user_id': '555', 'course_id': '999', 'skill_id': 'Recursion', 'badge_level': 'expert',
             'earned_at': datetime(2026, 9, 1)},
        ]
        ann = await self.student('555')
        self.assertEqual(ann['skillBreakdown']['Loops']['badgeLevel'], 'advanced')
        self.assertEqual(ann['skillBreakdown']['Loops']['badgeEarnedAt'], '2026-10-01T00:00:00')
        self.assertNotIn('badgeLevel', ann['skillBreakdown']['Recursion'])  # other course's badge
        self.assertEqual(ann['badgesEarned'], 1)
        self.assertEqual((await self.student('556'))['badgesEarned'], 0)

    async def test_class_averages_and_distribution(self):
        self.mastery.docs = [mastery('555', 'Loops', 80), mastery('556', 'Loops', 45),
                             mastery('555', 'Recursion', 33.33)]
        result = await self.analytics()
        self.assertEqual(result['averageScores'], {'Loops': 62.5, 'Recursion': 16.7})
        self.assertEqual(result['skillDistribution'], {'Loops': 2, 'Recursion': 2})

    async def test_debug_counts(self):
        self.mastery.docs = [mastery('555', 'Loops', 80), mastery('555', 'Recursion', 40)]
        debug = (await self.analytics())['debug']
        self.assertEqual((debug['realRecordCount'], debug['realAssignmentCount'], debug['queryCourseId']),
                         (2, 2, '100'))

    async def test_assignment_count_falls_back_to_the_older_collection(self):
        self.question_skills.docs = []
        self.db[Config.ACHIEVEUP_SKILL_MATRICES_COLLECTION].docs = [
            {'course_id': '100', 'matrix_id': 'm-1'}, {'course_id': '100'}, {'course_id': '999', 'matrix_id': 'm-9'}]
        self.db[Config.ACHIEVEUP_SKILL_ASSIGNMENTS_COLLECTION].docs = [
            {'matrix_id': 'm-1'}, {'matrix_id': 'm-1'}, {'matrix_id': 'm-9'}]
        debug = (await self.analytics())['debug']
        self.assertEqual(debug['realAssignmentCount'], 2)

    async def test_empty_roster(self):
        self.roster.return_value = []
        result = await self.analytics()
        self.assertEqual((result['students'], result['skillDistribution'], result['averageScores']), ([], {}, {}))

    async def test_roster_error_is_returned(self):
        self.roster.return_value = {'error': 'Failed to fetch students: 502', 'statusCode': 502}
        result = await analytics_service.get_course_students_analytics('t', '100')
        self.assertEqual(result, {'error': 'Failed to fetch students: 502', 'statusCode': 502})

    async def test_database_error_is_a_generic_500(self):
        broken = MagicMock()
        broken.__getitem__.side_effect = Exception('db down')
        with patch('services.analytics_service.db', broken), patch('traceback.print_exc'), \
             self.assertLogs('services.analytics_service', 'ERROR'):
            result = await analytics_service.get_course_students_analytics('t', '100')
        self.assertEqual(result, {'error': 'Internal server error', 'statusCode': 500})


if __name__ == '__main__':
    unittest.main()
