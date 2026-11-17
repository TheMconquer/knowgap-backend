import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable

from utils import youtube_utils

VIDEO_ID = 'dQw4w9WgXcQ'
CHANNEL_ID = 'UC' + 'a' * 22

def http_error(status=403):
    return HttpError(SimpleNamespace(status=status, reason='Forbidden'), b'{"error": {"message": "quota"}}')

class FakeYouTube:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def _list(self, **kwargs):
        self.calls.append(kwargs)
        request = MagicMock()
        if isinstance(self.reply, Exception):
            request.execute.side_effect = self.reply
        else:
            request.execute.return_value = self.reply
        return request

    def channels(self):
        return SimpleNamespace(list=self._list)

    def search(self):
        return SimpleNamespace(list=self._list)


class YouTubeClientTestCase(unittest.IsolatedAsyncioTestCase):
    def youtube(self, reply):
        self.client = FakeYouTube(reply)
        patcher = patch('utils.youtube_utils.get_youtube_client', return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)


class ResolveChannelTest(YouTubeClientTestCase):
    async def test_handle_is_looked_up_with_an_at_sign(self):
        self.youtube({'items': [{'id': CHANNEL_ID, 'snippet': {'title': 'Crash Course'}}]})
        for typed in ('crashcourse', '@crashcourse', '  @crashcourse  '):
            with self.subTest(typed=typed):
                self.assertEqual(await youtube_utils.resolve_channel(typed),
                                 {'channel_id': CHANNEL_ID, 'name': 'Crash Course'})
                self.assertEqual(self.client.calls[-1], {'part': 'id,snippet', 'forHandle': '@crashcourse'})

    async def test_channel_id_is_checked_too(self):
        self.youtube({'items': [{'id': CHANNEL_ID, 'snippet': {'title': 'Crash Course'}}]})
        await youtube_utils.resolve_channel(CHANNEL_ID)
        self.assertEqual(self.client.calls[-1], {'part': 'id,snippet', 'id': CHANNEL_ID})

    async def test_text_that_only_starts_like_an_id_is_a_handle(self):
        self.youtube({'items': []})
        await youtube_utils.resolve_channel('UCLA')
        self.assertEqual(self.client.calls[-1]['forHandle'], '@UCLA')

    async def test_unknown_channel_is_none(self):
        self.youtube({'items': []})
        self.assertIsNone(await youtube_utils.resolve_channel('@nobody'))

    async def test_lookup_failures_are_raised_not_reported_as_missing(self):
        for error in (http_error(), RuntimeError('network down')):
            with self.subTest(error=type(error).__name__):
                self.youtube(error)
                with self.assertLogs(level='ERROR'), self.assertRaises(type(error)):
                    await youtube_utils.resolve_channel('@crashcourse')


class ChannelSearchTest(YouTubeClientTestCase):
    async def test_results_are_mapped(self):
        self.youtube({'items': [{'id': {'videoId': VIDEO_ID}, 'snippet': {
            'title': 'Loops &amp; More', 'channelTitle': 'CS Dojo',
            'thumbnails': {'high': {'url': 'https://i.ytimg.com/hq.jpg'}}}}]})
        result = await youtube_utils.fetch_videos_for_topic_and_channel('for loops', CHANNEL_ID, limit=2)
        self.assertEqual(result, [{'title': 'Loops & More', 'link': f'https://www.youtube.com/watch?v={VIDEO_ID}',
                                   'channel': 'CS Dojo', 'thumbnail': 'https://i.ytimg.com/hq.jpg'}])
        self.assertEqual(self.client.calls[0], {'q': 'for loops', 'channelId': CHANNEL_ID, 'type': 'video',
                                                'part': 'id,snippet', 'maxResults': 2})

    async def test_missing_snippet_fields_get_placeholders(self):
        self.youtube({'items': [{'id': {'videoId': VIDEO_ID}, 'snippet': {}}]})
        result = await youtube_utils.fetch_videos_for_topic_and_channel('x', CHANNEL_ID)
        self.assertEqual((result[0]['title'], result[0]['channel'], result[0]['thumbnail']),
                         ('No Title Found', 'No Channel Found', ''))

    async def test_no_results_or_errors_are_an_empty_list(self):
        for reply in ({'items': []}, {}):
            with self.subTest(reply=reply), self.assertLogs(level='WARNING'):
                self.youtube(reply)
                self.assertEqual(await youtube_utils.fetch_videos_for_topic_and_channel('x', CHANNEL_ID), [])
        for reply in (http_error(), RuntimeError('down'), {'items': [{'snippet': {}}]}):
            with self.subTest(reply=reply), self.assertLogs(level='ERROR'):
                self.youtube(reply)
                self.assertEqual(await youtube_utils.fetch_videos_for_topic_and_channel('x', CHANNEL_ID), [])


class TopicSearchTest(unittest.IsolatedAsyncioTestCase):
    async def search(self, results=None, error=None):
        search = MagicMock()
        if error:
            search.side_effect = error
        else:
            search.return_value.result.return_value = {'result': results}
        with patch('utils.youtube_utils.VideosSearch', search):
            found = await youtube_utils.fetch_videos_for_topic('recursion', limit=2)
        return found, search

    async def test_results_are_mapped_and_limited(self):
        video = {'title': 'A &quot;great&quot; talk', 'link': 'https://www.youtube.com/watch?v=1',
                 'channel': {'name': 'CS'}, 'thumbnails': [{'url': 'https://i.ytimg.com/1.jpg'}]}
        found, search = await self.search([video, dict(video, title='two'), dict(video, title='three')])
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0], {'title': 'A "great" talk', 'link': 'https://www.youtube.com/watch?v=1',
                                    'channel': 'CS', 'thumbnail': 'https://i.ytimg.com/1.jpg'})
        search.assert_called_once_with('recursion', limit=2)

    async def test_no_results_or_errors_are_an_empty_list(self):
        with self.assertLogs(level='WARNING'):
            self.assertEqual((await self.search([]))[0], [])
        with self.assertLogs(level='ERROR'):
            self.assertEqual((await self.search(error=RuntimeError('blocked')))[0], [])


class ExtractVideoIdTest(unittest.TestCase):
    def test_ids_are_found(self):
        for link in (f'https://www.youtube.com/watch?v={VIDEO_ID}', f'https://youtu.be/{VIDEO_ID}',
                     f'https://www.youtube.com/watch?v={VIDEO_ID}&t=90s'):
            with self.subTest(link=link):
                self.assertEqual(youtube_utils.extract_video_id(link), VIDEO_ID)

    def test_no_id(self):
        self.assertIsNone(youtube_utils.extract_video_id('https://www.youtube.com/'))

    def test_metadata_text_is_unescaped(self):
        self.assertEqual(youtube_utils.clean_metadata_text('Tom &amp; Jerry&#39;s'), "Tom & Jerry's")


class TranscriptTest(unittest.IsolatedAsyncioTestCase):
    async def transcript(self, video, snippets=None, error=None):
        api = MagicMock()
        if error:
            api.return_value.fetch.side_effect = error
        else:
            api.return_value.fetch.return_value = snippets
        with patch('utils.youtube_utils.YouTubeTranscriptApi', api):
            result = await youtube_utils.fetch_video_transcript(video)
        return result, api

    async def test_transcript(self):
        snippets = [SimpleNamespace(text='hello', start=0.0, duration=2.0),
                    SimpleNamespace(text='world', start=2.0, duration=1.5)]
        result, api = await self.transcript(f'https://www.youtube.com/watch?v={VIDEO_ID}', snippets)
        self.assertEqual(result, {
            'success': True, 'transcript_text': 'hello world', 'language': 'en', 'video_id': VIDEO_ID,
            'transcript': [{'text': 'hello', 'start': 0.0, 'duration': 2.0},
                           {'text': 'world', 'start': 2.0, 'duration': 1.5}]})
        api.return_value.fetch.assert_called_once_with(VIDEO_ID, languages=['en'])

    async def test_bare_video_id_is_used_as_is(self):
        _, api = await self.transcript(VIDEO_ID, [])
        api.return_value.fetch.assert_called_once_with(VIDEO_ID, languages=['en'])

    async def test_empty_transcript_has_no_language(self):
        result, _ = await self.transcript(VIDEO_ID, [])
        self.assertIsNone(result['language'])

    async def test_youtube_link_without_an_id(self):
        result, api = await self.transcript('https://www.youtube.com/')
        self.assertEqual(result, {'success': False, 'error': 'Invalid video ID or URL'})
        api.assert_not_called()

    async def test_known_transcript_problems_have_their_own_messages(self):
        for error, message in [
            (TranscriptsDisabled(VIDEO_ID), 'Transcripts are disabled for this video'),
            (NoTranscriptFound(VIDEO_ID, ['en'], MagicMock()), "No transcript found for this video in languages: ['en']"),
            (VideoUnavailable(VIDEO_ID), 'Video is unavailable or does not exist'),
        ]:
            with self.subTest(error=type(error).__name__):
                result, _ = await self.transcript(VIDEO_ID, error=error)
                self.assertEqual(result, {'success': False, 'error': message})


class YouTubeClientCacheTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(youtube_utils, '_youtube_clients', threading.local())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_client_is_built_once_per_thread(self):
        with patch('utils.youtube_utils.build', side_effect=lambda *a, **kw: object()) as build:
            first = youtube_utils.get_youtube_client()
            self.assertIs(youtube_utils.get_youtube_client(), first)
            other = []
            thread = threading.Thread(target=lambda: other.append(youtube_utils.get_youtube_client()))
            thread.start()
            thread.join()
        self.assertIsNot(other[0], first)
        self.assertEqual(build.call_count, 2)
        self.assertEqual(build.call_args.args, ('youtube', 'v3'))


if __name__ == '__main__':
    unittest.main()
