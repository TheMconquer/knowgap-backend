import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from services import skill_video_service

INSTRUCTOR = {'id': 'inst-1', 'role': 'instructor', 'canvasTokenType': 'instructor'}
STUDENT = {'id': 'stu-1', 'role': 'student', 'canvasTokenType': 'student'}

def async_rows(rows):
    async def results():
        for row in rows:
            yield dict(row)
    return results()


def video(video_id, source='ai_suggested', prioritize=False, **overrides):
    return {'_id': video_id, 'course_id': '100', 'matrix_id': 'm-1', 'skill_name': 'Loops',
            'title': video_id, 'link': f'https://www.youtube.com/watch?v={video_id:_<11}', 'channel': 'CS',
            'thumbnail': '', 'source': source, 'prioritize': prioritize, 'status': 'published',
            'added_by': 'inst-1', 'created_at': datetime(2026, 9, 1), 'updated_at': datetime(2026, 9, 1),
            **overrides}

def patch_all(test, targets):
    for target, value in targets:
        patcher = patch(target, value)
        patcher.start()
        test.addCleanup(patcher.stop)
class SkillVideoListTest(unittest.IsolatedAsyncioTestCase):
    """Access and matrix-id rules are in test_skill_video_access."""

    def setUp(self):
        self.stored = []
        self.votes_rows = []
        self.student_votes = []
        self.queries = []
        videos = MagicMock()

        def find(query):
            self.queries.append(query)
            cursor = MagicMock()
            cursor.sort.return_value = async_rows(self.stored)
            return cursor
        videos.find = MagicMock(side_effect=find)
        votes = MagicMock()
        votes.aggregate = MagicMock(side_effect=lambda pipeline: async_rows(self.votes_rows))
        votes.find = MagicMock(side_effect=lambda query: async_rows(self.student_votes))
        self.votes = votes
        matrices = MagicMock()
        matrices.find_one = AsyncMock(return_value={'_id': 'm-1', 'course_id': '100'})
        patch_all(self, [
            ('services.skill_video_service.achieveup_verify_token', AsyncMock(return_value={'user': STUDENT})),
            ('services.skill_video_service.skill_videos_collection', videos),
            ('services.skill_video_service.skill_video_votes_collection', votes),
            ('services.skill_video_service.achieveup_skill_matrices_collection', matrices),
        ])

    async def videos(self):
        return await skill_video_service.get_skill_videos('t', '100', 'Loops', 'm-1')

    async def test_instructor_videos_first_then_linked_channels_then_other_ai_picks(self):
        #stored oldest first, as the database sort returns them.
        self.stored = [video('ai-1'), video('chan-1', prioritize=True), video('man-1', 'manual'),
                       video('ai-2'), video('man-2', 'manual'), video('chan-2', prioritize=True)]
        result = await self.videos()
        self.assertEqual([v['_id'] for v in result['videos']],
                         ['man-1', 'man-2', 'chan-1', 'chan-2', 'ai-1', 'ai-2'])

    async def test_only_published_videos_for_this_skill_are_asked_for(self):
        await self.videos()
        self.assertEqual(self.queries, [{'course_id': '100', 'matrix_id': 'm-1', 'skill_name': 'Loops',
                                         'status': 'published'}])

    async def test_votes_are_counted_per_video(self):
        self.stored = [video('v1'), video('v2')]
        self.votes_rows = [{'_id': {'video_id': 'v1', 'vote_type': 'upvote'}, 'count': 4},
                           {'_id': {'video_id': 'v1', 'vote_type': 'downvote'}, 'count': 1},
                           {'_id': {'video_id': 'gone', 'vote_type': 'upvote'}, 'count': 9}]
        self.student_votes = [{'video_id': 'v1', 'vote_type': 'upvote', 'student_id': 'stu-1'}]
        by_id = {v['_id']: v for v in (await self.videos())['videos']}
        self.assertEqual((by_id['v1']['upvotes'], by_id['v1']['downvotes'], by_id['v1']['student_vote']),
                         (4, 1, 'upvote'))
        self.assertEqual((by_id['v2']['upvotes'], by_id['v2']['downvotes'], by_id['v2']['student_vote']),
                         (0, 0, None))

    async def test_viewers_own_votes_are_looked_up(self):
        self.stored = [video('v1')]
        await self.videos()
        self.votes.find.assert_called_once_with({'video_id': {'$in': ['v1']}, 'student_id': 'stu-1'})

    async def test_shown_fields(self):
        self.stored = [video('v1', 'manual', timestamp_seconds=90)]
        shown = (await self.videos())['videos'][0]
        self.assertEqual(shown['created_at'], '2026-09-01T00:00:00')
        self.assertEqual(shown['timestamp_seconds'], 90)
        self.assertFalse(shown['instructor_verified'])
        self.assertNotIn('added_by', shown)

    async def test_no_videos_means_no_vote_lookups(self):
        self.assertEqual((await self.videos())['videos'], [])
        self.votes.aggregate.assert_not_called()

    async def test_login_error_is_returned(self):
        with patch('services.skill_video_service.achieveup_verify_token',
                   AsyncMock(return_value={'error': 'Invalid token', 'statusCode': 401})):
            self.assertEqual((await self.videos())['statusCode'], 401)


class ResolveMatrixIdTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.matrices = MagicMock()
        patch_all(self, [('services.skill_video_service.achieveup_skill_matrices_collection', self.matrices)])

    async def test_without_an_id_the_courses_matrix_for_the_skill_is_used(self):
        self.matrices.find_one = AsyncMock(return_value={'_id': 'm-loops'})
        self.assertEqual(await skill_video_service._resolve_matrix_id('100', None, 'Loops'), 'm-loops')
        self.matrices.find_one.assert_awaited_once_with({'course_id': '100', 'skills': 'Loops'})

    async def test_skill_in_no_matrix_uses_primary(self):
        self.matrices.find_one = AsyncMock(return_value=None)
        self.assertEqual(await skill_video_service._resolve_matrix_id('100', None, 'Loops'), 'primary')


class VoteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.videos = MagicMock()
        self.videos.find_one = AsyncMock(return_value=video('v1'))
        self.votes = MagicMock()
        self.votes.update_one = AsyncMock()
        self.votes.delete_one = AsyncMock()
        patch_all(self, [
            ('services.skill_video_service.achieveup_verify_token', AsyncMock(return_value={'user': STUDENT})),
            ('services.skill_video_service.skill_videos_collection', self.videos),
            ('services.skill_video_service.skill_video_votes_collection', self.votes),
        ])

    async def test_vote_replaces_the_students_earlier_vote(self):
        result = await skill_video_service.vote_skill_video('t', 'v1', 'downvote')
        self.assertEqual(result, {'success': True, 'message': 'Vote recorded successfully'})
        query, update = self.votes.update_one.await_args.args
        self.assertEqual(query, {'student_id': 'stu-1', 'video_id': 'v1'})
        self.assertEqual(update['$set']['vote_type'], 'downvote')
        self.assertEqual(update['$setOnInsert'], {'student_id': 'stu-1', 'video_id': 'v1', 'course_id': '100',
                                                  'matrix_id': 'm-1', 'skill_name': 'Loops'})
        self.assertTrue(self.votes.update_one.await_args.kwargs['upsert'])

    async def test_removing_a_vote(self):
        result = await skill_video_service.vote_skill_video('t', 'v1', 'remove')
        self.assertEqual(result['message'], 'Vote removed successfully')
        self.votes.delete_one.assert_awaited_once_with({'student_id': 'stu-1', 'video_id': 'v1'})
        self.votes.update_one.assert_not_awaited()

    async def test_unknown_vote_type(self):
        for vote_type in ('like', 'UPVOTE', ''):
            with self.subTest(vote_type=vote_type):
                self.assertEqual((await skill_video_service.vote_skill_video('t', 'v1', vote_type))['statusCode'], 400)
        self.videos.find_one.assert_not_awaited()

    async def test_unknown_video(self):
        self.videos.find_one.return_value = None
        self.assertEqual((await skill_video_service.vote_skill_video('t', 'nope', 'upvote'))['statusCode'], 404)
        self.votes.update_one.assert_not_awaited()


class TranscriptMomentTest(unittest.TestCase):
    def segment(self, start, text):
        return {'start': start, 'duration': 5, 'text': text}

    def test_busiest_thirty_second_window_wins(self):
        transcript = [self.segment(0, 'welcome'), self.segment(31, 'a for loop'),
                      self.segment(65, 'loop loop loop'), self.segment(89.9, 'loop again')]
        self.assertEqual(skill_video_service._find_best_transcript_bucket(transcript, 'Loop'), 60)

    def test_every_word_of_the_skill_counts(self):
        transcript = [self.segment(0, 'binary trees'), self.segment(30, 'search')]
        self.assertEqual(skill_video_service._find_best_transcript_bucket(transcript, 'Binary-Search Trees'), 0)

    def test_first_window_wins_a_tie(self):
        transcript = [self.segment(10, 'recursion'), self.segment(40, 'recursion')]
        self.assertEqual(skill_video_service._find_best_transcript_bucket(transcript, 'Recursion'), 0)

    def test_no_mention_or_no_words(self):
        transcript = [self.segment(0, 'nothing relevant')]
        self.assertIsNone(skill_video_service._find_best_transcript_bucket(transcript, 'Graphs'))
        self.assertIsNone(skill_video_service._find_best_transcript_bucket(transcript, ' - '))


class FindMomentAndVerifyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.video = video('v1', skill_name='Loops')
        self.videos = MagicMock()
        self.videos.find_one_and_update = AsyncMock(side_effect=lambda query, update, **kw: {
            **self.video, **update['$set']})
        self.transcript = AsyncMock(return_value={'success': True, 'transcript': [
            {'start': 0, 'text': 'intro'}, {'start': 95, 'text': 'loops explained'}]})
        patch_all(self, [
            ('services.skill_video_service._get_video_for_course_instructor',
             AsyncMock(side_effect=lambda token, video_id: (self.video, None))),
            ('services.skill_video_service.skill_videos_collection', self.videos),
            ('services.skill_video_service.fetch_video_transcript', self.transcript),
        ])

    async def test_moment_is_saved_on_the_video(self):
        result = await skill_video_service.find_relevant_moment('t', 'v1')
        self.assertEqual(result['timestamp_seconds'], 90)
        self.transcript.assert_awaited_once_with(self.video['link'])
        query, update = self.videos.find_one_and_update.await_args.args
        self.assertEqual((query, update['$set']['timestamp_seconds']), ({'_id': 'v1'}, 90))

    async def test_video_without_captions(self):
        self.transcript.return_value = {'success': False, 'error': 'Transcripts are disabled for this video'}
        result = await skill_video_service.find_relevant_moment('t', 'v1')
        self.assertEqual((result['statusCode'], result['message']), (422, 'Transcripts are disabled for this video'))
        self.videos.find_one_and_update.assert_not_awaited()

    async def test_captions_never_mention_the_skill(self):
        self.video['skill_name'] = 'Graphs'
        result = await skill_video_service.find_relevant_moment('t', 'v1')
        self.assertEqual(result['statusCode'], 422)
        self.videos.find_one_and_update.assert_not_awaited()

    async def test_only_ai_suggested_videos_can_be_verified(self):
        self.video = video('v1', 'manual')
        result = await skill_video_service.verify_ai_video('t', 'v1')
        self.assertEqual(result['statusCode'], 400)
        self.videos.find_one_and_update.assert_not_awaited()

    async def test_verifying_keeps_the_source(self):
        result = await skill_video_service.verify_ai_video('t', 'v1')
        self.assertEqual((result['instructor_verified'], result['source']), (True, 'ai_suggested'))


def found(*names):
    return [{'title': n, 'link': f'https://www.youtube.com/watch?v={n:_<11}', 'channel': 'CS',
             'thumbnail': 'https://i.ytimg.com/x.jpg'} for n in names]


class AiSuggestionsTest(unittest.IsolatedAsyncioTestCase):
    """Access rules for suggestions are in test_skill_video_access."""

    def setUp(self):
        self.existing = []
        self.videos = MagicMock()
        self.videos.find = MagicMock(side_effect=lambda query, projection=None: async_rows(self.existing))
        self.videos.insert_many = AsyncMock()
        self.matrices = MagicMock()
        self.matrices.find_one = AsyncMock(return_value={'_id': 'm-1', 'course_id': '100',
                                                         'course_name': 'Intro Programming', 'matrix_name': 'Week 1'})
        self.descriptions = MagicMock()
        self.descriptions.find_one = AsyncMock(return_value={'description': 'Saved description'})
        mappings = MagicMock()
        mappings.find.return_value.limit.return_value = async_rows([])
        self.topics = AsyncMock(return_value={'success': True, 'topics': ['for loops', 'while loops', 'nested loops']})
        self.channels = AsyncMock(return_value=[])
        self.channel_search = AsyncMock(side_effect=lambda topic, channel_id, limit: found(f'{channel_id}-{topic}'[:11]))
        self.global_search = AsyncMock(side_effect=lambda topic, limit: found(*[f'g{i}-{topic}'[:11] for i in range(limit)]))
        patch_all(self, [
            ('services.skill_video_service.require_course_instructor', AsyncMock(return_value=(INSTRUCTOR, None))),
            ('services.skill_video_service.skill_videos_collection', self.videos),
            ('services.skill_video_service.achieveup_skill_matrices_collection', self.matrices),
            ('services.skill_video_service.achieveup_course_descriptions_collection', self.descriptions),
            ('services.skill_video_service.achieveup_question_skills_collection', mappings),
            ('services.skill_video_service.generate_skill_search_topics', self.topics),
            ('services.skill_video_service.get_saved_course_channels', self.channels),
            ('services.skill_video_service.fetch_videos_for_topic_and_channel', self.channel_search),
            ('services.skill_video_service.fetch_videos_for_topic', self.global_search),
        ])

    async def suggest(self, count=6):
        return await skill_video_service.generate_ai_video_recommendations('t', '100', 'Loops', 'm-1', count=count)

    def inserted(self):
        return self.videos.insert_many.await_args.args[0]

    async def test_without_linked_channels_youtube_search_fills_the_list(self):
        result = await self.suggest(count=4)
        self.assertEqual(len(result['videos']), 4)
        self.global_search.assert_awaited_once_with(topic='for loops', limit=4)
        self.channel_search.assert_not_awaited()
        docs = self.inserted()
        self.assertTrue(all(d['source'] == 'ai_suggested' and d['status'] == 'published' and not d['prioritize']
                            for d in docs))
        self.assertTrue(all((d['course_id'], d['matrix_id'], d['skill_name'], d['added_by'])
                            == ('100', 'm-1', 'Loops', 'inst-1') for d in docs))

    async def test_linked_channels_are_searched_first_and_marked(self):
        self.channels.return_value = [{'channel_id': 'UCa', 'name': 'A'}, {'channel_id': 'UCb', 'name': 'B'}]
        await self.suggest(count=6)
        prioritized = [d['prioritize'] for d in self.inserted()]
        self.assertEqual(prioritized[:3], [True, True, True])
        self.assertFalse(any(prioritized[3:]))
        self.assertEqual(self.channel_search.await_args.kwargs['limit'], 3)  # count spread across channels

    async def test_channel_searches_are_capped(self):
        self.channels.return_value = [{'channel_id': c, 'name': c} for c in ('UCa', 'UCb', 'UCc')]
        await self.suggest(count=50)
        self.assertEqual(self.channel_search.await_count, Config.MAX_CHANNEL_SEARCHES)

    async def test_videos_already_on_the_skill_are_skipped(self):
        self.existing = [{'link': found('g0-for loop')[0]['link']}]
        await self.suggest(count=2)
        links = [d['link'] for d in self.inserted()]
        self.assertNotIn(self.existing[0]['link'], links)
        self.assertEqual(len(links), 2)

    async def test_later_topics_are_used_when_earlier_ones_run_dry(self):
        self.global_search.side_effect = [found('only-one'), found('second'), found('third')]
        result = await self.suggest(count=3)
        self.assertEqual(len(result['videos']), 3)
        self.assertEqual(self.global_search.await_count, 3)

    async def test_nothing_found_saves_nothing(self):
        self.global_search.side_effect = lambda topic, limit: []
        self.assertEqual(await self.suggest(), {'videos': []})
        self.videos.insert_many.assert_not_awaited()

    async def test_prompt_uses_course_name_and_saved_description(self):
        await self.suggest()
        self.topics.assert_awaited_once_with('Loops', 'Intro Programming', 'Saved description', [])

    async def test_matrix_name_stands_in_before_the_first_sync(self):
        self.matrices.find_one.return_value = {'_id': 'm-1', 'course_id': '100', 'matrix_name': 'Week 1'}
        await self.suggest()
        self.assertEqual(self.topics.await_args.args[1], 'Week 1')

    async def test_canvas_description_is_used_when_none_is_saved(self):
        self.descriptions.find_one.return_value = None
        details = AsyncMock(return_value={'public_description': '', 'syllabus_body': 'Syllabus text'})
        with patch('services.skill_video_service.get_user_canvas_token', AsyncMock(return_value='canvas-token')), \
             patch('services.achieveup_canvas_service.get_course_detailed_info', details):
            await self.suggest()
        self.assertEqual(self.topics.await_args.args[2], 'Syllabus text')
        details.assert_awaited_once_with('canvas-token', '100')

    async def test_suggestions_still_work_without_canvas(self):
        self.descriptions.find_one.return_value = None
        with patch('services.skill_video_service.get_user_canvas_token', AsyncMock(return_value=None)):
            result = await self.suggest()
        self.assertEqual(self.topics.await_args.args[2], '')
        self.assertEqual(len(result['videos']), 6)


if __name__ == '__main__':
    unittest.main()
