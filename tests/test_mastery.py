import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import mastery_service

class FakeQuestionSkills:
    #Question to skill mappings
    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def find(self, query):
        self.queries.append(query)
        wanted = query['question_id']['$in']

        async def results():
            for doc in self.docs:
                if doc.get('question_id') in wanted:
                    yield dict(doc)
        return results()


def submission(*questions, student_id='555'):
    return {'student_id': student_id, 'student_name': 'Ann', 'course_id': '100',
            'questions': [{'question_id': q, 'correct': c} for q, c in questions]}


class TallyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.skills = FakeQuestionSkills([
            {'question_id': 'q1', 'skills': ['Loops']},
            {'question_id': 'q2', 'skills': ['Loops'], 'matrix_id': 'm-1'},
            {'question_id': 'q3', 'skill_id': 'Arrays', 'skills': None},  # older single skill shape
        ])
        patcher = patch('services.mastery_service.question_skills_collection', self.skills)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def tally(self, *submissions):
        tally = mastery_service.CourseMasteryTally()
        for data in submissions:
            await tally.add_submission(data)
        return tally

    async def test_mappings_without_a_matrix_count_under_primary(self):
        tally = await self.tally(submission(('q1', True)))
        self.assertEqual(tally.totals, {('555', 'Loops', 'primary'): {'correct': 1, 'total': 1}})

    async def test_skills_are_kept_apart_by_matrix(self):
        tally = await self.tally(submission(('q1', True), ('q2', False)))
        self.assertEqual(tally.totals[('555', 'Loops', 'primary')], {'correct': 1, 'total': 1})
        self.assertEqual(tally.totals[('555', 'Loops', 'm-1')], {'correct': 0, 'total': 1})

    async def test_students_are_kept_apart(self):
        tally = await self.tally(submission(('q1', True)), submission(('q1', False), student_id='556'))
        self.assertEqual(tally.totals[('556', 'Loops', 'primary')], {'correct': 0, 'total': 1})
        self.assertEqual(tally.totals[('555', 'Loops', 'primary')], {'correct': 1, 'total': 1})

    async def test_older_single_skill_mappings_still_count(self):
        tally = await self.tally(submission(('q3', True)))
        self.assertEqual(tally.totals, {('555', 'Arrays', 'primary'): {'correct': 1, 'total': 1}})

    async def test_unmapped_questions_are_ignored(self):
        tally = await self.tally(submission(('q9', True)))
        self.assertEqual(tally.totals, {})

    async def test_only_this_submissions_questions_are_looked_up(self):
        await self.tally(submission(('q1', True), ('q2', True)))
        self.assertEqual(self.skills.queries, [{'question_id': {'$in': ['q1', 'q2']}}])

    async def test_submission_without_questions_needs_no_lookup(self):
        tally = await self.tally(submission())
        self.assertEqual((tally.totals, self.skills.queries), ({}, []))

    async def test_submission_without_a_student_is_skipped(self):
        with self.assertLogs('services.mastery_service', 'WARNING'):
            tally = await self.tally(submission(('q1', True), student_id=None))
        self.assertEqual((tally.totals, self.skills.queries), ({}, []))

    async def test_student_names_are_kept_for_badges(self):
        tally = await self.tally(submission(('q1', True)), submission(('q1', True), student_id='556') | {'student_name': None})
        self.assertEqual(tally.student_names, {'555': 'Ann'})

    async def test_lookup_failure_is_raised_not_skipped(self):
        broken = MagicMock()
        broken.find.side_effect = Exception('db down')
        tally = mastery_service.CourseMasteryTally()
        with patch('services.mastery_service.question_skills_collection', broken), \
             self.assertRaisesRegex(Exception, 'db down'):
            await tally.add_submission(submission(('q1', True)))
        self.assertEqual(tally.totals, {})


class ReplaceCourseMasteryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mastery = MagicMock()
        self.mastery.bulk_write = AsyncMock()
        self.mastery.delete_many = AsyncMock()
        self.award = AsyncMock()
        for target, value in [
            ('services.mastery_service.mastery_collection', self.mastery),
            ('services.mastery_service.check_and_award_badge', self.award),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tally(self, totals, names=None):
        tally = mastery_service.CourseMasteryTally()
        tally.totals = totals
        tally.student_names = names or {}
        return tally

    async def test_rows_are_upserted_per_student_skill_and_matrix(self):
        count = await mastery_service.replace_course_mastery(100, 'Intro', self.tally({
            ('555', 'Loops', 'm-1'): {'correct': 3, 'total': 4},
            ('556', 'Loops', 'm-1'): {'correct': 0, 'total': 2},
        }))
        self.assertEqual(count, 2)
        operations = self.mastery.bulk_write.await_args.args[0]
        self.assertEqual([op._filter for op in operations], [
            {'student_id': '555', 'course_id': '100', 'skill_id': 'Loops', 'matrix_id': 'm-1'},
            {'student_id': '556', 'course_id': '100', 'skill_id': 'Loops', 'matrix_id': 'm-1'}])
        self.assertTrue(all(op._upsert for op in operations))
        self.assertEqual(operations[0]._doc['$set']['skill_name'], 'Loops')
        self.assertEqual(operations[0]._doc['$set']['mastery_percentage'], 75)
        self.assertEqual(self.mastery.bulk_write.await_args.kwargs, {'ordered': False})

    async def test_empty_tally_still_clears_the_courses_old_rows(self):
        #for ex, every skill was unassigned since the last sync
        self.assertEqual(await mastery_service.replace_course_mastery('100', 'Intro', self.tally({})), 0)
        self.mastery.bulk_write.assert_not_awaited()
        query = self.mastery.delete_many.await_args.args[0]
        self.assertEqual(query['course_id'], '100')
        self.award.assert_not_awaited()

    async def test_each_sync_marks_its_rows_with_a_new_id(self):
        totals = {('555', 'Loops', 'm-1'): {'correct': 1, 'total': 1}}
        await mastery_service.replace_course_mastery('100', 'Intro', self.tally(totals))
        await mastery_service.replace_course_mastery('100', 'Intro', self.tally(totals))
        first, second = (c.args[0]['sync_id']['$ne'] for c in self.mastery.delete_many.await_args_list)
        self.assertNotEqual(first, second)

    async def test_skill_in_two_matrices_is_judged_once_on_its_best_score(self):
        await mastery_service.replace_course_mastery('100', 'Intro', self.tally({
            ('555', 'Loops', 'm-1'): {'correct': 1, 'total': 4},
            ('555', 'Loops', 'm-2'): {'correct': 9, 'total': 10},
        }, {'555': 'Ann'}))
        self.award.assert_awaited_once_with('555', '100', 'Loops', 90.0, 'Ann', 'Intro')

    async def test_one_failed_badge_does_not_stop_the_others(self):
        self.award.side_effect = [Exception('db down'), None]
        with self.assertLogs('services.mastery_service', 'ERROR'):
            await mastery_service.replace_course_mastery('100', 'Intro', self.tally({
                ('555', 'Loops', 'm-1'): {'correct': 1, 'total': 1},
                ('556', 'Loops', 'm-1'): {'correct': 1, 'total': 1},
            }))
        self.assertEqual(self.award.await_count, 2)


class AwardBadgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.badges = MagicMock()
        self.badges.find_one = AsyncMock(return_value=None)
        self.create = AsyncMock()
        for target, value in [
            ('services.badge_service.achieveup_user_badges_collection', self.badges),
            ('services.badge_service.create_badge_for_student', self.create),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_badge_for_the_reached_tier(self):
        await mastery_service.check_and_award_badge('555', '100', 'Loops', 80.0, 'Ann', 'Intro')
        self.badges.find_one.assert_awaited_once_with(
            {'user_id': '555', 'skill_id': 'Loops', 'badge_level': 'advanced', 'course_id': '100'})
        self.create.assert_awaited_once_with('555', '100', 'Loops', 'advanced', 80.0, 'Ann', 'Intro')

    async def test_no_badge_below_the_first_tier(self):
        await mastery_service.check_and_award_badge('555', '100', 'Loops', 24.9)
        self.badges.find_one.assert_not_awaited()
        self.create.assert_not_awaited()

    async def test_same_tier_is_never_awarded_twice(self):
        self.badges.find_one.return_value = {'badge_id': 'b-1'}
        await mastery_service.check_and_award_badge('555', '100', 'Loops', 95.0)
        self.create.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
