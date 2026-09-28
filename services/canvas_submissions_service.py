# services/canvas_submissions_service.py

"""
Canvas Quiz Submissions Service
================================

Fetches and processes student quiz submissions from Canvas API.
Provides real student performance data for badge generation and analytics.
"""

import aiohttp
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
from services.achieveup_canvas_service import create_canvas_session, CANVAS_API_URL, is_new_quiz
from config import Config
import csv
import io

from mongodb import get_db

# Set up logging
logger = logging.getLogger(__name__)

# MongoDB setup
db = get_db()

submissions_collection = db.get_collection('AchieveUp_Quiz_Submissions')

async def check_rate_limit(response):
    """
    Check Canvas rate limit headers and pause if running low.
    Canvas returns X-Rate-Limit-Remaining to indicate budget left.
    """
    remaining = response.headers.get('X-Rate-Limit-Remaining')
    if remaining is not None:
        try:
            remaining_val = float(remaining)
            if remaining_val < 50:
                wait_time = max(5, int(60 - remaining_val))
                logger.warning(f"Canvas rate limit low ({remaining_val} remaining). Pausing {wait_time}s...")
                await asyncio.sleep(wait_time)
            elif remaining_val < 100:
                # Light throttle when getting close
                await asyncio.sleep(1)
        except (ValueError, TypeError):
            pass

async def get_student_quiz_submission(canvas_token: str, course_id: str, quiz_id: str, student_id: str) -> dict:
    """
    Fetch a specific student's quiz submission from Canvas API.
    
    Args:
        canvas_token: Canvas API token
        course_id: Canvas course ID
        quiz_id: Canvas quiz ID
        student_id: Canvas student/user ID
        
    Returns:
        dict: Submission data with questions and scores
    """
    try:
        headers = {
            'Authorization': f'Bearer {canvas_token}',
            'Content-Type': 'application/json'
        }

        # Get quiz submission
        url = f"{CANVAS_API_URL}/courses/{course_id}/quizzes/{quiz_id}/submissions"
        params = {
            'user_id': student_id,
            'include[]': ['submission', 'quiz', 'user']
        }

        async with create_canvas_session() as session:
            async with session.get(url, headers=headers, params=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Canvas submission fetch error: {response.status} - {error_text}")
                    return {
                        'error': f'Failed to fetch submission: {response.status}',
                        'statusCode': response.status
                    }

                data = await response.json()

                # Canvas returns submissions in 'quiz_submissions' array
                submissions = data.get('quiz_submissions', [])
                if not submissions:
                    return {
                        'error': 'No submission found',
                        'message': 'Student has not submitted this quiz',
                        'statusCode': 404
                    }

                # Get the most recent submission
                submission = submissions[0]

                # Fetch detailed submission data with questions
                submission_id = submission.get('id')
                questions_url = f"{CANVAS_API_URL}/quiz_submissions/{submission_id}/questions"

                async with session.get(questions_url, headers=headers) as q_response:
                    if q_response.status == 200:
                        questions_data = await q_response.json()
                        submission['questions'] = questions_data.get('quiz_submission_questions', [])
                    else:
                        logger.warning(f"Could not fetch submission questions: {q_response.status}")
                        submission['questions'] = []

                return submission

    except aiohttp.ClientError as e:
        logger.error(f"Canvas API connection error: {str(e)}")
        return {
            'error': 'Canvas API connection failed',
            'message': str(e),
            'statusCode': 500
        }
    except Exception as e:
        logger.error(f"Get student quiz submission error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def get_all_course_submissions(canvas_token: str, course_id: str, quiz_id: str) -> dict:
    """
    Fetch all student submissions for a quiz in a course.
    
    Args:
        canvas_token: Canvas API token
        course_id: Canvas course ID
        quiz_id: Canvas quiz ID
        
    Returns:
        dict: List of all submissions
    """
    try:
        headers = {
            'Authorization': f'Bearer {canvas_token}',
            'Content-Type': 'application/json'
        }
        
        url = f"{CANVAS_API_URL}/courses/{course_id}/quizzes/{quiz_id}/submissions"
        new_quiz_url: str = f"{CANVAS_API_URL}/courses/{course_id}/assignments/{quiz_id}/submissions"

        all_submissions: list = []
        
        async with create_canvas_session() as session:
            is_canvas_new_quiz: bool | None = None

            match await is_new_quiz(canvas_token, course_id, quiz_id):
                case True:
                    is_canvas_new_quiz = True
                    cont: bool = True
                    params = {
                        'per_page': 100
                    }

                    # While loop to ensure response pages are also parsed.
                    while cont:
                        async with session.get(new_quiz_url, headers=headers, params=params) as response:
                            if response.status != 200:
                                error_text = await response.text()
                                logger.error(f"Canvas submissions fetch error: {response.status} - {error_text}")
                                return {
                                    'error': f'Failed to fetch submissions: {response.status}',
                                    'statusCode': response.status
                                }
                            
                            data = await response.json()

                            if "errors" in data:
                                logger.error(f"Canvas' REST API call returned an error: {data['errors']}")
                                return {
                                    "error": "Canvas API call error.",
                                    "message": "An error occured when attempting to communicate with Canvas' API.",
                                    "statusCode": 400
                                }

                            # Mapping new quiz fields to the same fields classic quizzes returns.
                            all_submissions.extend([
                                {
                                    'attempt': submission.get('attempt'),
                                    'attempts_left': None,
                                    'end_at': submission.get('cached_due_date'),
                                    'excused?': submission.get('excused'),
                                    'extra_attempts': submission.get('extra_attempts'),
                                    'extra_time': None,
                                    'finished_at': submission.get('submitted_at'),
                                    'fudge_points': None,
                                    'has_seen_results': None,
                                    'html_url': submission.get('preview_url') or submission.get('url'),
                                    'id': submission.get('id'),
                                    'kept_score': submission.get('score') if submission.get('score') is not None else submission.get('entered_score'),
                                    'manually_unlocked': None,
                                    'overdue_and_needs_submission': submission.get('missing'),
                                    'quiz_id': submission.get('assignment_id', quiz_id),
                                    'quiz_points_possible': None,
                                    'quiz_version': None,
                                    'result_url': submission.get('preview_url'),
                                    'score': submission.get('score'),
                                    'score_before_regrade': None,
                                    'started_at': None,
                                    'submission_id': submission.get('id'),
                                    'time_spent': None,
                                    'user_id': submission.get('user_id'),
                                    'validation_token': None,
                                    'workflow_state': submission.get('workflow_state'),
                                }
                                for submission in data
                                if submission.get("attempt")
                            ])

                            await check_rate_limit(response)
                                
                            # Check for next page
                            link_header = response.headers.get('Link', '')
                            if 'rel="next"' in link_header:
                                # Parse next URL from Link header
                                for link in link_header.split(','):
                                    if 'rel="next"' in link:
                                        new_quiz_url = link.split(';')[0].strip('<> ')
                                        break
                            else:
                                cont = False
                            
                            params = {}

                case False:
                    is_canvas_new_quiz = False
                    params = {
                        'per_page': 100,
                        'include[]': ['submission', 'user']
                    }

                    cont: bool = True
                    while cont:
                        async with session.get(url, headers=headers, params=params) as response:
                            if response.status != 200:
                                error_text = await response.text()
                                logger.error(f"Canvas submissions fetch error: {response.status} - {error_text}")
                                return {
                                    'error': f'Failed to fetch submissions: {response.status}',
                                    'statusCode': response.status
                                }
                            
                            data = await response.json()
                            submissions = data.get('quiz_submissions', [])
                            all_submissions.extend(submissions)

                            # Check rate limit headers before continuing
                            
                            await check_rate_limit(response)
                            
                            # Check for next page
                            link_header = response.headers.get('Link', '')
                            if 'rel="next"' in link_header:
                                # Parse next URL from Link header
                                for link in link_header.split(','):
                                    if 'rel="next"' in link:
                                        url = link.split(';')[0].strip('<> ')
                                        break
                            else:
                                cont = False
                            
                            params = {}  # Clear params for subsequent requests (URL has them)


                case None:
                    logger.error(f"Failed to determine quiz type: {course_id}, quiz_id: {quiz_id}")
                    return {'error': f'Failed to determine instructor quiz type.', 'statusCode': 404}
        
        return {
            'submissions': all_submissions,
            'count': len(all_submissions),
            "is_new_quiz": is_canvas_new_quiz
        }
        
    except Exception as e:
        logger.error(f"Get all course submissions error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}


async def process_submission_data(submission: dict) -> dict:
    """
    Process Canvas submission data into standardized format.
    
    Args:
        submission: Raw Canvas submission data
        
    Returns:
        dict: Processed submission with question scores
    """
    try:
        processed = {
            'submission_id': str(submission.get('id')),
            'student_id': str(submission.get('user_id')),
            'student_name': submission.get('user', {}).get('name') or submission.get('user', {}).get('display_name'),
            'quiz_id': str(submission.get('quiz_id')),
            'attempt': submission.get('attempt', 1),
            'score': submission.get('score', 0),
            'kept_score': submission.get('kept_score', 0),
            'score_before_regrade': submission.get('score_before_regrade'),
            'points_possible': submission.get('quiz_points_possible', 0),
            'submitted_at': submission.get('submitted_at'),
            'started_at': submission.get('started_at'),
            'finished_at': submission.get('finished_at'),
            'workflow_state': submission.get('workflow_state'),
            'questions': []
        }
        
        # Process individual questions
        for question in submission.get('questions', []):
            question_data = {
                'question_id': str(question.get('id')),
                'question_type': question.get('question_type'),
                'points': question.get('points', 0),
                'points_possible': question.get('points_possible', 0),
                'correct': question.get('correct', False),
                'answer': question.get('answer'),
                'correct_answer': question.get('correct_answer')
            }
            processed['questions'].append(question_data)
        
        return processed
        
    except Exception as e:
        logger.error(f"Process submission data error: {str(e)}")
        return {}

async def store_submission_data(course_id: str, submission_data: dict, force_cache: bool = False) -> bool:
    """
    Store processed submission data in MongoDB for caching.
    
    Args:
        course_id: Canvas course ID
        submission_data: Processed submission data
        force_cache: If True, always cache. If False, only cache if ENABLE_SUBMISSION_CACHE is True
        
    Returns:
        bool: Success status
    """
    try:
        # Check if caching is enabled (default: False to minimize storage)
        enable_cache = getattr(Config, 'ENABLE_SUBMISSION_CACHE', False)
        
        if not enable_cache and not force_cache:
            logger.debug("Submission caching disabled, skipping storage")
            return False
        
        submission_data['course_id'] = course_id
        submission_data['cached_at'] = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Upsert submission (update if exists, insert if new)
        await submissions_collection.update_one(
            {
                'submission_id': submission_data['submission_id'],
                'student_id': submission_data['student_id'],
                'quiz_id': submission_data['quiz_id']
            },
            {'$set': submission_data},
            upsert=True
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Store submission data error: {str(e)}")
        return False

async def get_cached_submission(student_id: str, course_id: str, quiz_id: str) -> Optional[dict]:
    """
    Retrieve cached submission from MongoDB.
    Only used if caching is enabled.
    
    Args:
        student_id: Canvas student ID
        course_id: Canvas course ID
        quiz_id: Canvas quiz ID
        
    Returns:
        dict or None: Cached submission if exists and fresh
    """
    try:
        # Check if caching is enabled
        enable_cache = getattr(Config, 'ENABLE_SUBMISSION_CACHE', False)
        if not enable_cache:
            return None
        
        submission = await submissions_collection.find_one({
            'student_id': student_id,
            'course_id': course_id,
            'quiz_id': quiz_id
        }, {"_id": 0})
        
        if submission:
            # Check cache freshness (default: 1 hour)
            cache_ttl = int(getattr(Config, 'SUBMISSION_CACHE_TTL', 3600))
            cache_age = (datetime.now(timezone.utc).replace(tzinfo=None) - submission.get('cached_at', datetime.min)).total_seconds()
            
            if cache_age < cache_ttl:
                return submission
        
        return None
        
    except Exception as e:
        logger.error(f"Get cached submission error: {str(e)}")
        return None

async def get_student_submissions_for_course(token: str, student_id: str, course_id: str, use_cache: bool = False) -> dict:
    """
    Get all quiz submissions for a student in a course.
    By default, fetches directly from Canvas API to minimize storage.
    
    Args:
        token: AchieveUp auth token
        student_id: Canvas student ID
        course_id: Canvas course ID
        use_cache: If True, attempt to use cached data first
        
    Returns:
        dict: All submissions for the student in the course
    """
    try:
        # Verify token and get Canvas token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result
        
        user_id = user_result['user']['id']
        canvas_token = await get_user_canvas_token(user_id)
        
        if not canvas_token:
            return {
                'error': 'Canvas token not found',
                'message': 'Please connect your Canvas account',
                'statusCode': 400
            }
        
        # Get all quizzes in the course
        from services.achieveup_canvas_service import get_instructor_course_quizzes
        quizzes_result = await get_instructor_course_quizzes(canvas_token, course_id)
        
        if 'error' in quizzes_result:
            return quizzes_result
        
        quizzes = quizzes_result if isinstance(quizzes_result, list) else []
        
        # Fetch submissions for each quiz
        all_submissions = []
        for quiz in quizzes:
            quiz_id = quiz.get('id')
            
            # Try cache first only if explicitly requested
            if use_cache:
                cached = await get_cached_submission(student_id, course_id, quiz_id)
                if cached:
                    all_submissions.append(cached)
                    continue
            
            # Fetch directly from Canvas (no caching by default)
            submission = await get_student_quiz_submission(canvas_token, course_id, quiz_id, student_id)
            
            if 'error' not in submission:
                # Process submission
                processed = await process_submission_data(submission)
                if processed:
                    all_submissions.append(processed)
                    # Only cache if explicitly enabled
                    if use_cache:
                        await store_submission_data(course_id, processed)
        
        return {
            'student_id': student_id,
            'course_id': course_id,
            'submissions': all_submissions,
            'total_submissions': len(all_submissions)
        }
        
    except Exception as e:
        logger.error(f"Get student submissions for course error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def sync_course_submissions(token: str, course_id: str) -> dict:
    """
    Sync all submissions for a course from Canvas to local cache.
    Useful for batch updates and analytics.
    
    Args:
        token: AchieveUp auth token
        course_id: Canvas course ID
        
    Returns:
        dict: Sync status and statistics
    """
    try:
        # Verify token and get Canvas token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result
        
        user_id = user_result['user']['id']
        canvas_token = await get_user_canvas_token(user_id)
        
        if not canvas_token:
            return {
                'error': 'Canvas token not found',
                'message': 'Please connect your Canvas account',
                'statusCode': 400
            }
        
        return await sync_course_submissions_direct(canvas_token, course_id)
        
    except Exception as e:
        logger.error(f"Sync course submissions error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def sync_course_submissions_direct(canvas_token: str, course_id: str) -> dict:
    """
    Sync all submissions for a course using a raw Canvas token.
    Intended for background tasks (app.py) or internal calls.
    """
    try:
        # Get all quizzes in the course
        from services.achieveup_canvas_service import get_instructor_course_quizzes
        quizzes_result = await get_instructor_course_quizzes(canvas_token, course_id)
        
        if 'error' in quizzes_result:
            return quizzes_result
        
        quizzes = quizzes_result if isinstance(quizzes_result, list) else []
        
        if not quizzes:
            logger.info(f"No quizzes found for course {course_id}")
            return {
                'message': 'No quizzes found (Direct)',
                'course_id': course_id,
                'total_quizzes': 0,
                'total_synced': 0,
                'total_errors': 0,
                'progress_synced': 0,
                'synced_at': datetime.now(timezone.utc).replace(tzinfo=None)
            }

        total_synced = 0
        total_errors = 0
        
        async with create_canvas_session() as session:
            # Sync submissions for each quiz
            for quiz in quizzes:
                quiz_id = quiz.get('id')
                quiz_title = quiz.get('title', 'Unknown')
                
                # Get all submissions for this quiz
                submissions_result = await get_all_course_submissions(canvas_token, course_id, quiz_id)
                
                if 'error' in submissions_result:
                    total_errors += 1
                    logger.error(f"Failed to sync quiz {quiz_id} ({quiz_title}): {submissions_result.get('error')}")
                    continue
                
                submissions = submissions_result.get('submissions', [])
                
                # Process and store each submission
                from services.mastery_service import update_student_mastery, mastery_collection
                
                # Clear previous mastery data for this course to prevent $inc duplication 
                # upon every sync interval (since we aggregate across all time)
                if quiz == quizzes[0]: # Only do this once per course sync
                    await mastery_collection.delete_many({'course_id': str(course_id)})

                # Check if quiz is a new quiz. If it is, get the new quizzes data.
                if submissions_result.get("is_new_quiz") and submissions:
                    new_quiz_data = await get_new_quiz_data(canvas_token, course_id, quiz_id)

                    # Check for errors.
                    if "error" in new_quiz_data:
                        total_errors += 1

                        logger.error(f"Failed to get New Quiz results for quiz {quiz_id} ({quiz_title}): {new_quiz_data['error']}")

                        continue

                    new_quiz_submission_results = new_quiz_data.get("results")
                    for submission in submissions:
                        submission["questions"] = new_quiz_submission_results.get(str(submission.get("user_id")), [])
                
                # Internal helper for parallel question fetching
                semaphore = asyncio.Semaphore(10) # Limit concurrency to 10 requests

                async def fetch_and_process_submission(sub):
                    nonlocal total_synced
                    async with semaphore:
                        student_id = sub.get('user_id', 'unknown')
                        
                        # Fetch detailed submission data with questions if missing
                        if 'questions' not in sub:
                            submission_id = sub.get('id')
                            if submission_id:
                                questions_url = f"{CANVAS_API_URL}/quiz_submissions/{submission_id}/questions"
                                headers = {'Authorization': f'Bearer {canvas_token}'}
                                try:
                                    async with session.get(questions_url, headers=headers) as q_response:
                                        await check_rate_limit(q_response)
                                        if q_response.status == 200:
                                            questions_data = await q_response.json()
                                            sub['questions'] = questions_data.get('quiz_submission_questions', [])
                                        else:
                                            logger.warning(f"Could not fetch questions for submission {submission_id} (student {student_id}): status {q_response.status}")
                                except Exception as e:
                                    logger.error(f"Error fetching questions for submission {submission_id}: {str(e)}")
                        
                        processed = await process_submission_data(sub)
                        if processed:
                            processed['course_id'] = str(course_id)
                            # Update mastery tracking
                            await update_student_mastery(processed)
                            # Store raw submission
                            success = await store_submission_data(course_id, processed)
                            if success:
                                total_synced += 1

                # Execute all submissions for this quiz in parallel
                await asyncio.gather(*(fetch_and_process_submission(s) for s in submissions))
        
        # Check how many mastery docs exist after sync
        mastery_count = await mastery_collection.count_documents({'course_id': str(course_id)})
        
        # === Sync Progress collection from freshly-updated Mastery data ===
        # This ensures student charts/graphs reflect the latest skill assignments.
        # No extra Canvas API calls — we just read what mastery_service already wrote.
        progress_synced = 0
        try:
            progress_collection = db.get_collection('AchieveUp_Progress')

            # Read all mastery docs for this course (just written above)
            mastery_docs = await mastery_collection.find(
                {'course_id': str(course_id)}
            ).to_list(length=None)

            # Group by student_id
            student_mastery = {}
            for doc in mastery_docs:
                sid = doc.get('student_id')
                if not sid:
                    continue
                if sid not in student_mastery:
                    student_mastery[sid] = {}

                skill_id = doc.get('skill_id', 'Unknown')
                total_correct = doc.get('total_correct', 0)
                total_attempted = doc.get('total_attempted', 0)
                percentage = doc.get('mastery_percentage', 0)

                if percentage >= 80:
                    level = 'advanced'
                elif percentage >= 60:
                    level = 'intermediate'
                else:
                    level = 'beginner'

                student_mastery[sid][skill_id] = {
                    'score': round(percentage, 1),
                    'level': level,
                    'total_questions': total_attempted,
                    'correct_answers': total_correct,
                    'notes': ''
                }

            # Upsert one Progress document per student (overwrites, no bloat)
            for sid, skill_progress in student_mastery.items():
                await progress_collection.update_one(
                    {'student_id': sid, 'course_id': str(course_id)},
                    {'$set': {
                        'student_id': sid,
                        'course_id': str(course_id),
                        'skill_progress': skill_progress,
                        'last_updated': datetime.now(timezone.utc).replace(tzinfo=None)
                    }},
                    upsert=True
                )
                progress_synced += 1

            logger.info(f"Progress collection updated for {progress_synced} students in course {course_id}")
        except Exception as progress_error:
            logger.error(f"Error updating Progress collection: {str(progress_error)}")

        return {
            'message': 'Sync completed (Direct)',
            'course_id': course_id,
            'total_quizzes': len(quizzes),
            'total_synced': total_synced,
            'total_errors': total_errors,
            'progress_synced': progress_synced,
            'synced_at': datetime.now(timezone.utc).replace(tzinfo=None)
        }
        
    except Exception as e:
        logger.error(f"Sync course submissions direct error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def get_new_quiz_data(canvas_token: str, course_id: str, quiz_id: str) -> dict:
    "Get equivalent data of classic quiz for a new quiz."

    try:
        headers: dict = {"Authorization": f"Bearer {canvas_token}"}

        url: str = f"{getattr(Config, 'CANVAS_NEW_QUIZ_API_URL')}/courses/{course_id}/quizzes/{quiz_id}"

        # Get quiz questions for a new quiz.
        async with create_canvas_session() as session:
            async with session.get(f"{url}/items", headers=headers, params={"per_page": 100}) as res:
                if res.status != 200:
                    return {"error": f"Failed to get new quiz items: {res.status}", "statusCode": res.status}
                
                quiz_data: list = await res.json()

                items: list = [item for item in quiz_data if item.get("entry_type") != "Stimulus"]

                # Sort question items to align with report questions later.
                items_by_position: dict = {item.get("position"): item for item in items}
                items = [items_by_position[position] for position in sorted(items_by_position)]

            # Request body to get new quiz report.
            canvas_quiz_report_request: dict = {
                "quiz_report[report_type]": "student_analysis",
                "quiz_report[format]": "csv"
            }


            async with session.post(f"{url}/reports", headers=headers, data=canvas_quiz_report_request) as res:
                if res.status not in (200, 201):
                    return {"error": f"Failed to request new quiz report: {res.status}", "statusCode": res.status}
                
                progress = await res.json()

                report_progress: dict = progress.get("progress", {})

                # Repeatedly check report status.
                for i in range(120):

                    # Check if report is still generating.
                    if report_progress.get("workflow_state") not in ["completed", "failed"]:
                        await asyncio.sleep(3)
                        async with session.get(report_progress.get("url"), headers=headers) as resp:
                            report_progress = await resp.json()

                report_file_url: str = (report_progress.get("results") or {}).get("url", "")

                if report_progress.get("workflow_state", "") != "completed" or not report_file_url:
                    return {"error": "New quiz report did not finish.", "statusCode": 502}
                
                # Download new quiz report.
                async with session.get(report_file_url, headers=headers) as res:
                    report_data: str = (await res.text(encoding="utf-8")).lstrip("\ufeff")

                    if res.status != 200 or report_data.startswith('{"errors"'):
                        logger.error(f"Failed to download new quiz report for quiz {quiz_id}: {report_data[:300]}")
                        return {"error": "Failed to download new quiz report.", "statusCode": 502}
                

        # Use CSV to read the report.
        report_file = io.StringIO(report_data)
        file_rows: list = list(csv.reader(report_file))

        report_headers: list = file_rows[0]

        question_columns: list = [i for i, name in enumerate(report_headers) if name == "ItemID"]

        if len(question_columns) != len(items):
            return {"error": "New quiz report does not match the quiz's questions.", "statusCode": 502}

        question_results: dict = {}
        latest_attempts: dict = {}

        # Parse every question row in the CSV.
        for row in file_rows[1:]:
            student_id: str = row[report_headers.index("ID")]
            attempt: int = int(row[report_headers.index("Attempt")] or 0)

            # Check if the attempt is the latest attempt.
            if attempt < latest_attempts.get(student_id, 0):
                continue

            latest_attempts[student_id] = attempt

            questions: list = []

            # Parse all question columns.
            for item, column in zip(items, question_columns):
                earned_points: float = float(row[column + 3] or 0)
                points_possible: float = float(item.get("points_possible") or 0)
                status: str = row[column + 4]

                # Skip answers that are still waiting on manual grading.
                if status != "Graded":
                    continue

                questions.append({
                    "id": str(item.get("id")),
                    "question_text": (item.get("entry") or {}).get("item_body"),
                    "correct": points_possible > 0 and earned_points >= points_possible,
                })

            question_results[student_id] = questions

        return {"results": question_results}

    except Exception as error:
        logger.error(f"Error trying to retrieve new quiz data: {str(error)}")
        return {"error": "Internal server error.", "statusCode": 500}