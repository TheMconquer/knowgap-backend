import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from services import achieveup_ai_service as ai_service
from services import achieveup_service
from utils import ai_utils
from utils.ai_utils import AI_MODEL

INSTRUCTOR = {'id': 'inst-1', 'role': 'instructor', 'canvasTokenType': 'instructor'}

def chat_reply(content):
    #An OpenAI chat completion whose message is `content`
    message = MagicMock(content=content)
    return MagicMock(choices=[MagicMock(message=message)])


def fake_client(*contents):
    #The shared AI gateway client
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[chat_reply(c) for c in contents])
    return client

class ClassifyQuestionsTest(unittest.IsolatedAsyncioTestCase):
    async def classify(self, reply, questions=('Q0', 'Q1'), skills=('Loops', 'Recursion', 'Arrays', 'Graphs')):
        client = fake_client(reply)
        self.create = client.chat.completions.create
        with patch('services.achieveup_ai_service.client', client):
            return await ai_service.classify_questions_batch_ai(list(questions), list(skills))

    async def test_only_listed_skills_are_kept(self):
        result = await self.classify(json.dumps({'0': ['Loops', 'Made Up'], '1': ['Recursion']}))
        self.assertEqual(result, {'0': ['Loops'], '1': ['Recursion']})

    async def test_at_most_three_skills(self):
        result = await self.classify(json.dumps({'0': ['Loops', 'Recursion', 'Arrays', 'Graphs']}))
        self.assertEqual(result, {'0': ['Loops', 'Recursion', 'Arrays']})

    async def test_answers_that_arent_lists_are_dropped(self):
        result = await self.classify(json.dumps({'0': 'Loops', '1': None, '2': ['Graphs']}))
        self.assertEqual(result, {'2': ['Graphs']})

    async def test_prompt_lists_skills_and_numbered_questions(self):
        await self.classify('{}', questions=('First?', 'x' * 900))
        kwargs = self.create.await_args.kwargs
        prompt = kwargs['messages'][1]['content']
        self.assertIn('Available Skills: Loops, Recursion, Arrays, Graphs', prompt)
        self.assertIn('0. First?', prompt)
        self.assertIn('1. ' + 'x' * 500 + '\n', prompt)  # long questions are cut to 500 characters
        self.assertNotIn('x' * 501, prompt)
        self.assertEqual(kwargs['response_format'], {'type': 'json_object'})
        self.assertEqual(kwargs['model'], AI_MODEL)

    async def test_json_wrapped_in_a_code_fence_is_found(self):
        result = await self.classify('Sure!\n```json\n{"0": ["Loops"]}\n```')
        self.assertEqual(result, {'0': ['Loops']})

    async def test_reply_that_isnt_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            await self.classify('Sorry, I cannot help with that.')

class AnalyzeQuestionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.batches = []

        async def classify(texts, skills):
            self.batches.append(texts)
            return {'0': ['Loops'], 1: ['Recursion']}
        patcher = patch('services.achieveup_ai_service.classify_questions_batch_ai', classify)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_questions_are_sent_in_batches_of_twenty(self):
        questions = [{'id': i, 'question_text': f'Q{i}'} for i in range(45)]
        results = await ai_service.analyze_questions(questions, ['Loops', 'Recursion'])
        self.assertEqual([len(batch) for batch in self.batches], [20, 20, 5])
        self.assertEqual(len(results), 45)
        self.assertEqual([r['questionId'] for r in results], list(range(45)))

    async def test_each_question_gets_its_own_batch_answer(self):
        questions = [{'id': 'a', 'question_text': 'A'}, {'id': 'b', 'text': 'B'}, {'id': 'c'}]
        results = await ai_service.analyze_questions(questions, ['Loops'])
        self.assertEqual([r['suggestedSkills'] for r in results], [['Loops'], ['Recursion'], []])
        self.assertEqual(self.batches, [['A', 'B', '']])
        self.assertIn('analysis_timestamp', results[0])

    async def test_skills_are_required(self):
        for skills in (None, []):
            with self.subTest(skills=skills), self.assertRaises(ValueError):
                await ai_service.analyze_questions([{'id': 1}], skills)
        self.assertEqual(self.batches, [])

    async def test_gateway_key_and_url_are_required(self):
        for setting in ('AI_API_KEY', 'AI_API_URL'):
            with self.subTest(setting=setting), patch.object(Config, setting, None), \
                 self.assertRaises(RuntimeError):
                await ai_service.analyze_questions([{'id': 1}], ['Loops'])
        self.assertEqual(self.batches, [])

    async def test_no_questions(self):
        self.assertEqual(await ai_service.analyze_questions([], ['Loops']), [])


class SearchTopicsTest(unittest.IsolatedAsyncioTestCase):
    async def topics(self, reply, **kwargs):
        client = MagicMock()
        if isinstance(reply, Exception):
            client.chat.completions.create = AsyncMock(side_effect=reply)
        else:
            client.chat.completions.create = AsyncMock(return_value=chat_reply(reply))
        self.create = client.chat.completions.create
        with patch('utils.ai_utils.client', client), patch('builtins.print'):
            return await ai_utils.generate_skill_search_topics('Recursion', 'Intro Programming', **kwargs)

    async def test_numbering_bullets_and_quotes_are_removed(self):
        result = await self.topics('1. recursion basics\n\n- base case examples\n"call stack explained"')
        self.assertEqual(result, {'success': True,
                                  'topics': ['recursion basics', 'base case examples', 'call stack explained']})

    async def test_at_most_three_topics(self):
        result = await self.topics('a\nb\nc\nd\ne')
        self.assertEqual(result['topics'], ['a', 'b', 'c'])

    async def test_empty_reply_is_a_failure(self):
        self.assertEqual(await self.topics('  \n 1. \n - '), {'success': False, 'error': 'No topics generated'})

    async def test_gateway_error_is_a_failure(self):
        result = await self.topics(Exception('rate limited'))
        self.assertEqual(result, {'success': False, 'error': 'rate limited'})

    async def test_prompt_includes_course_context_and_up_to_three_sample_questions(self):
        await self.topics('topic', course_context='Python course', sample_questions=['q1', 'q2', 'q3', 'q4'])
        prompt = self.create.await_args.kwargs['messages'][0]['content']
        self.assertIn('"Recursion" in the course Intro Programming', prompt)
        self.assertIn('course context: Python course', prompt)
        self.assertIn('q1 | q2 | q3', prompt)
        self.assertNotIn('q4', prompt)
        self.assertEqual(self.create.await_args.kwargs['model'], AI_MODEL)

    async def test_prompt_without_extra_context(self):
        await self.topics('topic')
        prompt = self.create.await_args.kwargs['messages'][0]['content']
        self.assertNotIn('course context', prompt)
        self.assertNotIn('example quiz questions', prompt)


class SuggestCourseSkillsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.descriptions = MagicMock()
        self.descriptions.find_one = AsyncMock(return_value=None)
        for target, value in [
            ('services.achieveup_service.require_course_instructor', AsyncMock(return_value=(INSTRUCTOR, None))),
            ('services.achieveup_service.achieveup_course_descriptions_collection', self.descriptions),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def suggest(self, reply, **course):
        client = fake_client(reply)
        self.create = client.chat.completions.create
        data = {'courseId': '100', 'courseName': 'Intro', 'courseCode': 'COP1000', **course}
        with patch('services.achieveup_service.client', client):
            return await achieveup_service.suggest_course_skills_ai('t', data)

    def prompt(self):
        return self.create.await_args.kwargs['messages'][1]['content']

    async def test_skills_are_parsed_from_the_reply(self):
        skills = [{'skill': 'Write loops', 'description': 'for and while'}]
        result = await self.suggest(json.dumps(skills))
        self.assertEqual(result['skills'], skills)
        self.assertEqual((result['courseId'], result['courseName'], result['method']), ('100', 'Intro', 'ai'))
        self.assertEqual(self.create.await_args.kwargs['model'], AI_MODEL)

    async def test_json_wrapped_in_text_or_code_fences_is_found(self):
        result = await self.suggest('Here you go:\n```json\n[{"skill": "Recursion", "description": "d"}]\n```')
        self.assertEqual(result['skills'], [{'skill': 'Recursion', 'description': 'd'}])

    async def test_saved_description_wins_over_the_request(self):
        self.descriptions.find_one.return_value = {'description': '  Saved description  '}
        result = await self.suggest('[]', courseDescription='Request description')
        self.assertIn('Description: Saved description', self.prompt())
        self.assertEqual(result['courseDescriptionSource'], 'stored')
        self.descriptions.find_one.assert_awaited_once_with(
            {'course_id': '100', 'instructor_id': 'inst-1'}, {'description': 1, '_id': 0})

    async def test_request_description_is_used_when_none_is_saved(self):
        result = await self.suggest('[]', courseDescription='Request description')
        self.assertIn('Description: Request description', self.prompt())
        self.assertEqual(result['courseDescriptionSource'], 'request')

    async def test_no_description_at_all(self):
        await self.suggest('[]')
        self.assertIn('Description: N/A', self.prompt())

    async def test_unreadable_reply_is_a_generic_500(self):
        with patch('traceback.print_exc'), self.assertLogs('services.achieveup_service', 'ERROR'):
            result = await self.suggest('I cannot do that')
        self.assertEqual(result['statusCode'], 500)
        self.assertEqual(result['message'], 'An unexpected error occurred')


if __name__ == '__main__':
    unittest.main()
