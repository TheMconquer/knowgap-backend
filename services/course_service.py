from utils.course_utils import get_quiz_questions, get_course_name, clean_text, get_quizzes
from config import Config
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timezone

from mongodb import get_db

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB setup
db = get_db()

quizzes_collection = db[Config.QUIZZES_COLLECTION]

async def update_quiz_questions_per_course(course_id, access_token, link):
    """Updates the database with quiz questions for a course."""
    try:
        # Clean the link to remove protocol
        clean_link = link.replace("https://", "").replace("http://", "")
        
        # Get quizzes for the course
        quizlist, quizname = await get_quizzes(course_id, access_token, clean_link)
        if not quizlist:
            # logger.warning(f"No quizzes found for course {course_id}")
            return {'status': 'Success', 'message': 'No quizzes found'}

        # Get course name
        course_name = await get_course_name(course_id, access_token, clean_link)
        if not course_name:
            # logger.error(f"Failed to get course name for course {course_id}")
            return {'status': 'Error', 'error': 'Failed to get course name'}

        # Process each quiz
        for quiz_id, quiz_title in zip(quizlist, quizname):
            try:
                # Get questions for this quiz
                questions = await get_quiz_questions(course_id, quiz_id, access_token, clean_link)
                if not questions:
                    # logger.warning(f"No questions found for quiz {quiz_id}")
                    continue

                # Save each question to the database
                for question in questions:
                    try:
                        # Clean question text
                        question_text = BeautifulSoup(question["question_text"], "html.parser").get_text()
                        cleaned_text = clean_text(question_text)

                        # Save to database
                        await quizzes_collection.update_one(
                            {
                                'quizid': int(quiz_id),
                                'courseid': str(course_id),
                                'questionid': str(question["id"])
                            },
                            {
                                "$set": {
                                    'course_name': course_name,
                                    'quiz_name': quiz_title,
                                    'question_text': cleaned_text,
                                    'question_type': question.get("question_type", "unknown"),
                                    'updated_at': datetime.now(timezone.utc)
                                }
                            },
                            upsert=True
                        )
                    except Exception as e:
                        # logger.error(f"Error saving question {question.get('id')} for quiz {quiz_id}: {e}")
                        continue

            except Exception as e:
                # logger.error(f"Error processing quiz {quiz_id}: {e}")
                if "403" in str(e):
                    # logger.error(f"Quiz {quiz_id} ({quiz_title}) is not accessible - likely due to permissions or quiz status")
                    # logger.error(f"Consider checking if the quiz is published and accessible to the current user role")
                    pass
                continue

        return {'status': 'Success', 'message': 'Quiz questions updated successfully'}

    except Exception as e:
        # logger.error(f"Error in update_quiz_questions_per_course: {e}")
        return {'status': 'Error', 'error': str(e)}
