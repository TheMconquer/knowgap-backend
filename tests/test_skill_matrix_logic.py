import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from services import achieveup_service
from utils.text_utils import hash_text

INSTRUCTOR = {'id': 'inst-1', 'role': 'instructor', 'canvasTokenType': 'instructor'}

def matches(doc, query):
    for key, value in query.items():
        if isinstance(value, dict) and '$in' in value:
            if doc.get(key) not in value['$in']:
                return False
        elif doc.get(key) != value:
            return False
    return True

class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self.docs]

class FakeCollection:
    #In memory collection for the queries achieveup_service makes

    def __init__(self, docs=()):
        self.docs = [dict(doc) for doc in docs]

    async def find_one(self, query, projection=None):
        doc = next((d for d in self.docs if matches(d, query)), None)
        if doc and projection:
            doc = {k: v for k, v in doc.items() if projection.get(k) != 0}
        return dict(doc) if doc else None

    def find(self, query, projection=None):
        return FakeCursor([d for d in self.docs if matches(d, query)])

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        doc = next((d for d in self.docs if matches(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = dict(query)
            doc.update(update.get('$setOnInsert', {}))
            self.docs.append(doc)
        doc.update(update.get('$set', {}))
        for key, value in update.get('$addToSet', {}).items():
            doc.setdefault(key, [])
            if value not in doc[key]:
                doc[key].append(value)


def matrix(matrix_id, course_id, name='Matrix', skills=('Loops',)):
    return {'_id': matrix_id, 'course_id': course_id, 'matrix_name': name, 'skills': list(skills)}


class MatrixTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.matrices = FakeCollection()
        self.question_skills = FakeCollection()
        self.import_status = FakeCollection()
        self.user = INSTRUCTOR
        for target, value in [
            ('services.achieveup_service.require_course_instructor', AsyncMock(return_value=(INSTRUCTOR, None))),
            ('services.achieveup_service.achieveup_verify_token', AsyncMock(return_value={'user': INSTRUCTOR})),
            ('services.achieveup_service.achieveup_skill_matrices_collection', self.matrices),
            ('services.achieveup_service.achieveup_question_skills_collection', self.question_skills),
            ('services.achieveup_service.achieveup_import_status_collection', self.import_status),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class CreateMatrixTest(MatrixTestCase):
    async def test_matrix_document(self):
        result = await achieveup_service.create_skill_matrix('t', '100', 'Week 1', ['Loops', 'Recursion'])
        self.assertEqual(result['skills'], ['Loops', 'Recursion'])
        self.assertEqual(result['matrix_name'], 'Week 1')
        self.assertIsInstance(result['_id'], str)
        self.assertEqual(result['created_by'], 'inst-1')

    async def test_name_must_be_unique_within_a_course(self):
        await achieveup_service.create_skill_matrix('t', '100', 'Week 1', [])
        result = await achieveup_service.create_skill_matrix('t', '100', 'Week 1', ['Other'])
        self.assertEqual(result['statusCode'], 409)
        self.assertEqual(len(self.matrices.docs), 1)

    async def test_same_name_in_another_course_is_fine(self):
        await achieveup_service.create_skill_matrix('t', '100', 'Week 1', [])
        result = await achieveup_service.create_skill_matrix('t', '200', 'Week 1', [])
        self.assertNotIn('error', result)
        self.assertEqual(len(self.matrices.docs), 2)

    async def test_save_failure_is_a_generic_500(self):
        with patch.object(self.matrices, 'insert_one', AsyncMock(side_effect=Exception('db down'))), \
             self.assertLogs('services.achieveup_service', 'ERROR'):
            result = await achieveup_service.create_skill_matrix('t', '100', 'Week 1', [])
        self.assertEqual(result, {'error': 'Internal server error', 'statusCode': 500})


class ListMatricesTest(MatrixTestCase):
    async def test_ids_are_made_json_safe(self):
        object_id = ObjectId()
        self.matrices.docs = [matrix(object_id, '100'), matrix('m-2', '100')]
        result = await achieveup_service.get_all_skill_matrices_by_course('t', '100')
        self.assertEqual(result['count'], 2)
        self.assertEqual([m['_id'] for m in result['matrices']], [str(object_id), 'm-2'])

    async def test_course_with_no_matrices(self):
        self.matrices.docs = [matrix('m-1', '999')]
        result = await achieveup_service.get_all_skill_matrices_by_course('t', '100')
        self.assertEqual(result, {'matrices': [], 'count': 0})


class AnalyzeQuestionsTest(MatrixTestCase):
    async def analyze(self, matrix_id=None):
        self.ai = AsyncMock(return_value=[{'questionId': 'q1', 'suggestedSkills': ['Loops']}])
        with patch('services.achieveup_ai_service.analyze_questions', self.ai):
            return await achieveup_service.analyze_questions_with_ai(
                't', [{'id': 'q1', 'question_text': 'Q'}], '100', matrix_id)

    async def test_chosen_matrix_is_used(self):
        self.matrices.docs = [matrix('m-1', '100', 'First', ['A']), matrix('m-2', '100', 'Second', ['B', 'C'])]
        result = await self.analyze('m-2')
        self.ai.assert_awaited_once_with([{'id': 'q1', 'question_text': 'Q'}], ['B', 'C'])
        self.assertEqual((result['matrix_id'], result['matrix_used'], result['skill_count'], result['totalQuestions']),
                         ('m-2', 'Second', 2, 1))
        self.assertEqual(result['analyses'], [{'questionId': 'q1', 'suggestedSkills': ['Loops']}])

    async def test_unknown_matrix_falls_back_to_the_courses_first_matrix(self):
        self.matrices.docs = [matrix('m-1', '100', 'First', ['A'])]
        result = await self.analyze('deleted')
        self.assertEqual((result['matrix_used'], result['course_skills_used']), ('First', ['A']))

    async def test_course_without_any_skills(self):
        for docs in ([], [matrix('m-1', '100', skills=())]):
            with self.subTest(docs=docs), self.assertLogs('services.achieveup_service', 'ERROR'):
                self.matrices.docs = docs
                result = await self.analyze()
                self.assertEqual(result['statusCode'], 404)
                self.ai.assert_not_awaited()

    async def test_ai_failure_is_a_generic_500(self):
        self.matrices.docs = [matrix('m-1', '100')]
        with patch('services.achieveup_ai_service.analyze_questions', AsyncMock(side_effect=Exception('quota'))), \
             patch('traceback.print_exc'), self.assertLogs('services.achieveup_service', 'ERROR'):
            result = await achieveup_service.analyze_questions_with_ai('t', [{'id': 'q1'}], '100')
        self.assertEqual(result, {'error': 'Internal server error', 'statusCode': 500})


class ImportMatricesTest(MatrixTestCase):
    async def test_every_source_matrix_is_copied(self):
        self.matrices.docs = [matrix('m-1', '50', 'Week 1', ['A']), matrix('m-2', '50', 'Week 2', ['B']),
                              matrix('m-3', '999', 'Not this one')]
        result = await achieveup_service.import_matrices_from_course('50', '100', 'inst-1', 't')
        self.assertEqual([m['matrix_name'] for m in result],
                         ['Week 1 (Imported Skills Matrix)', 'Week 2 (Imported Skills Matrix)'])
        self.assertEqual([m['skills'] for m in result], [['A'], ['B']])
        self.assertTrue(all(m['course_id'] == '100' for m in result))
        self.assertNotIn(result[0]['_id'], ('m-1', 'm-2'))

    async def test_import_status_records_matrices(self):
        self.matrices.docs = [matrix('m-1', '50')]
        await achieveup_service.import_matrices_from_course('50', '100', 'inst-1', 't')
        status = await achieveup_service.get_import_status('t', '100')
        self.assertEqual((status['source_course_id'], status['matrices_imported']), ('50', True))
        self.assertNotIn('assignments_imported', status)

    async def test_empty_source_course(self):
        result = await achieveup_service.import_matrices_from_course('50', '100', 'inst-1', 't')
        self.assertEqual(result, [])

class AssignedSkillsTest(MatrixTestCase):
    async def test_saved_skills_are_returned_under_the_key_asked_for(self):
        await achieveup_service.assign_skills_to_questions('t', '100', {'what is 2+2? 4 5': ['Addition']})
        result = await achieveup_service.get_assigned_skills('t', '100', ['what is 2+2? 4 5', 'unsaved'])
        self.assertEqual(result, {'course_id': '100', 'question_skills': {'what is 2+2? 4 5': ['Addition']}})

    async def test_mapping_is_shared_across_courses_of_the_same_instructor(self):
        await achieveup_service.assign_skills_to_questions('t', '100', {'key': ['A']})
        await achieveup_service.assign_skills_to_questions('t', '200', {'key': ['B']})
        self.assertEqual(len(self.question_skills.docs), 1)
        self.assertEqual(self.question_skills.docs[0]['course_ids'], ['100', '200'])
        self.assertEqual(self.question_skills.docs[0]['skills'], ['B'])  # latest save wins

    async def test_other_instructors_mappings_are_not_returned(self):
        self.question_skills.docs = [{'question_id': hash_text('key'), 'owner_id': 'inst-2', 'skills': ['X']}]
        result = await achieveup_service.get_assigned_skills('t', '100', ['key'])
        self.assertEqual(result['question_skills'], {})

    async def test_question_ids_are_required(self):
        result = await achieveup_service.get_assigned_skills('t', '100', [])
        self.assertEqual(result['statusCode'], 400)


class ImportStatusTest(MatrixTestCase):
    async def test_course_never_imported_into(self):
        self.assertEqual(await achieveup_service.get_import_status('t', '100'),
                         {'target_course_id': '100', 'matrices_imported': False})

    async def test_flag_from_the_removed_skill_import_is_hidden(self):
        self.import_status.docs = [{'_id': 'x', 'target_course_id': '100', 'source_course_id': '50',
                                    'matrices_imported': True, 'assignments_imported': True}]
        self.assertEqual(await achieveup_service.get_import_status('t', '100'),
                         {'target_course_id': '100', 'source_course_id': '50', 'matrices_imported': True})

    async def test_login_error_is_returned(self):
        with patch('services.achieveup_service.achieveup_verify_token',
                   AsyncMock(return_value={'error': 'Token expired', 'statusCode': 401})):
            self.assertEqual(await achieveup_service.get_import_status('t', '100'),
                             {'error': 'Token expired', 'statusCode': 401})


if __name__ == '__main__':
    unittest.main()
