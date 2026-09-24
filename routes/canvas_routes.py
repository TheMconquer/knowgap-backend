from quart import Blueprint, request, jsonify
from services.achieveup_canvas_service import (
    get_canvas_courses,
    get_canvas_course_quizzes,
    get_canvas_quiz_questions
)
import logging
logger = logging.getLogger(__name__)
canvas_bp = Blueprint('canvas', __name__)

@canvas_bp.route('/canvas/courses', methods=['GET'])
async def achieveup_canvas_courses_route():
    """Get user's Canvas courses. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Call Canvas service
        result = await get_canvas_courses(token)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'message': result['error'],
                'statusCode': result['statusCode']
            }), result['statusCode']
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/courses/<course_id>/quizzes', methods=['GET'])
async def achieveup_canvas_course_quizzes_route(course_id):
    """Get quizzes for a specific course. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Call Canvas service
        result = await get_canvas_course_quizzes(token, course_id)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'message': result['error'],
                'statusCode': result['statusCode']
            }), result['statusCode']
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/quizzes/<quiz_id>/questions', methods=['GET'])
async def achieveup_canvas_quiz_questions_route(quiz_id):
    """Get questions for a specific quiz. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Call Canvas service
        result = await get_canvas_quiz_questions(token, quiz_id)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'message': result['error'],
                'statusCode': result['statusCode']
            }), result['statusCode']
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/test-connection', methods=['GET'])
async def test_canvas_connection_route():
    """Test if stored Canvas API token is still valid. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Get user's stored Canvas API token and test it
        from services.achieveup_auth_service import get_user_canvas_token
        from services.achieveup_canvas_service import validate_canvas_token
        
        # First verify the user's JWT token
        from services.achieveup_auth_service import achieveup_verify_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({
                'error': user_result['error'],
                'message': user_result['error'],
                'statusCode': user_result['statusCode']
            }), user_result['statusCode']
        
        user_id = user_result['user']['id']
        
        # Get user's stored Canvas API token
        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({
                'connected': False,
                'message': 'No Canvas API token found. Please add your Canvas API token in settings.'
            }), 200
        
        # Test the stored token
        validation_result = await validate_canvas_token(canvas_token)
        
        if validation_result['valid']:
            return jsonify({
                'connected': True,
                'message': 'Successfully connected to Canvas',
                'user_info': validation_result.get('user_info')
            }), 200
        else:
            return jsonify({
                'connected': False,
                'message': validation_result['message']
            }), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/instructor/courses', methods=['GET'])
async def instructor_courses_route():
    """Get all courses taught by the instructor (instructor token required)."""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token', 'message': 'Authorization header with Bearer token is required', 'statusCode': 401}), 401
        token = auth_header.split(' ')[1]
        from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({'error': user_result['error'], 'message': user_result['error'], 'statusCode': user_result['statusCode']}), user_result['statusCode']
        user_id = user_result['user']['id']
        # Check instructor token type
        from motor.motor_asyncio import AsyncIOMotorClient
        from config import Config
        client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tlsAllowInvalidCertificates=(Config.ENV == 'development')
        )
        db = client[Config.DATABASE]
        user_doc = await db[Config.ACHIEVEUP_USERS_COLLECTION].find_one({'user_id': user_id})
        if not user_doc or user_doc.get('canvas_token_type', 'student') != 'instructor':
            return jsonify({'error': 'Forbidden', 'message': 'Instructor token required', 'statusCode': 403}), 403
        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({'error': 'No Canvas token', 'message': 'No Canvas API token found for user', 'statusCode': 400}), 400
        from services.achieveup_canvas_service import get_instructor_courses
        result = await get_instructor_courses(canvas_token)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500}), 500

@canvas_bp.route('/canvas/instructor/courses/<course_id>/quizzes', methods=['GET'])
async def instructor_course_quizzes_route(course_id):
    """Get all quizzes in a course (instructor token required)."""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token', 'message': 'Authorization header with Bearer token is required', 'statusCode': 401}), 401
        token = auth_header.split(' ')[1]
        from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({'error': user_result['error'], 'message': user_result['error'], 'statusCode': user_result['statusCode']}), user_result['statusCode']
        user_id = user_result['user']['id']
        from motor.motor_asyncio import AsyncIOMotorClient
        from config import Config
        client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tlsAllowInvalidCertificates=(Config.ENV == 'development')
        )
        db = client[Config.DATABASE]
        user_doc = await db[Config.ACHIEVEUP_USERS_COLLECTION].find_one({'user_id': user_id})
        if not user_doc or user_doc.get('canvas_token_type', 'student') != 'instructor':
            return jsonify({'error': 'Forbidden', 'message': 'Instructor token required', 'statusCode': 403}), 403
        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({'error': 'No Canvas token', 'message': 'No Canvas API token found for user', 'statusCode': 400}), 400
        from services.achieveup_canvas_service import get_instructor_course_quizzes
        result = await get_instructor_course_quizzes(canvas_token, course_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500}), 500

@canvas_bp.route('/canvas/instructor/courses/<course_id>/quizzes/<quiz_id>/questions', methods=['GET'])
async def instructor_quiz_questions_route(course_id, quiz_id):
    """Get all questions in a quiz (instructor token required)."""
    try:
        #debugging 
        logger.info(f"star route called: /canvas/instructor/courses/{course_id}/quizzes/{quiz_id}/questions")

        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token', 'message': 'Authorization header with Bearer token is required', 'statusCode': 401}), 401
        token = auth_header.split(' ')[1]
        from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({'error': user_result['error'], 'message': user_result['error'], 'statusCode': user_result['statusCode']}), user_result['statusCode']
        user_id = user_result['user']['id']
        from motor.motor_asyncio import AsyncIOMotorClient
        from config import Config
        client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tlsAllowInvalidCertificates=(Config.ENV == 'development')
        )
        db = client[Config.DATABASE]
        user_doc = await db[Config.ACHIEVEUP_USERS_COLLECTION].find_one({'user_id': user_id})
        if not user_doc or user_doc.get('canvas_token_type', 'student') != 'instructor':
            return jsonify({'error': 'Forbidden', 'message': 'Instructor token required', 'statusCode': 403}), 403
        
        #course_id = request.args.get('course_id')

        #debugging
        logger.info(f"star course_id from query params: {course_id}")
        if not course_id:
            return jsonify({'error': 'Missing Course_id', 'message': 'course_id query paramenter required', 'statusCode': 400}), 400

        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({'error': 'No Canvas token', 'message': 'No Canvas API token found for user', 'statusCode': 400}), 400
        from services.achieveup_canvas_service import get_instructor_quiz_questions
        result = await get_instructor_quiz_questions(canvas_token, quiz_id, course_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500}), 500 
    
from quart import Blueprint, request, jsonify
from services.achieveup_canvas_service import (
    get_canvas_courses,
    get_canvas_course_quizzes,
    get_canvas_quiz_questions
)
import logging
logger = logging.getLogger(__name__)
canvas_bp = Blueprint('canvas', __name__)

@canvas_bp.route('/canvas/courses', methods=['GET'])
async def achieveup_canvas_courses_route():
    """Get user's Canvas courses. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Call Canvas service
        result = await get_canvas_courses(token)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'message': result['error'],
                'statusCode': result['statusCode']
            }), result['statusCode']
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/courses/<course_id>/quizzes', methods=['GET'])
async def achieveup_canvas_course_quizzes_route(course_id):
    """Get quizzes for a specific course. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Call Canvas service
        result = await get_canvas_course_quizzes(token, course_id)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'message': result['error'],
                'statusCode': result['statusCode']
            }), result['statusCode']
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/quizzes/<quiz_id>/questions', methods=['GET'])
async def achieveup_canvas_quiz_questions_route(quiz_id):
    """Get questions for a specific quiz. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Call Canvas service
        result = await get_canvas_quiz_questions(token, quiz_id)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'message': result['error'],
                'statusCode': result['statusCode']
            }), result['statusCode']
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/test-connection', methods=['GET'])
async def test_canvas_connection_route():
    """Test if stored Canvas API token is still valid. (AchieveUp only)"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing token',
                'message': 'Authorization header with Bearer token is required',
                'statusCode': 401
            }), 401
        
        token = auth_header.split(' ')[1]
        
        # Get user's stored Canvas API token and test it
        from services.achieveup_auth_service import get_user_canvas_token
        from services.achieveup_canvas_service import validate_canvas_token
        
        # First verify the user's JWT token
        from services.achieveup_auth_service import achieveup_verify_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({
                'error': user_result['error'],
                'message': user_result['error'],
                'statusCode': user_result['statusCode']
            }), user_result['statusCode']
        
        user_id = user_result['user']['id']
        
        # Get user's stored Canvas API token
        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({
                'connected': False,
                'message': 'No Canvas API token found. Please add your Canvas API token in settings.'
            }), 200
        
        # Test the stored token
        validation_result = await validate_canvas_token(canvas_token)
        
        if validation_result['valid']:
            return jsonify({
                'connected': True,
                'message': 'Successfully connected to Canvas',
                'user_info': validation_result.get('user_info')
            }), 200
        else:
            return jsonify({
                'connected': False,
                'message': validation_result['message']
            }), 200
        
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred',
            'statusCode': 500
        }), 500

@canvas_bp.route('/canvas/instructor/courses', methods=['GET'])
async def instructor_courses_route():
    """Get all courses taught by the instructor (instructor token required)."""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token', 'message': 'Authorization header with Bearer token is required', 'statusCode': 401}), 401
        token = auth_header.split(' ')[1]
        from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({'error': user_result['error'], 'message': user_result['error'], 'statusCode': user_result['statusCode']}), user_result['statusCode']
        user_id = user_result['user']['id']
        # Check instructor token type
        from motor.motor_asyncio import AsyncIOMotorClient
        from config import Config
        client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tlsAllowInvalidCertificates=(Config.ENV == 'development')
        )
        db = client[Config.DATABASE]
        user_doc = await db[Config.ACHIEVEUP_USERS_COLLECTION].find_one({'user_id': user_id})
        if not user_doc or user_doc.get('canvas_token_type', 'student') != 'instructor':
            return jsonify({'error': 'Forbidden', 'message': 'Instructor token required', 'statusCode': 403}), 403
        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({'error': 'No Canvas token', 'message': 'No Canvas API token found for user', 'statusCode': 400}), 400
        from services.achieveup_canvas_service import get_instructor_courses
        result = await get_instructor_courses(canvas_token)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500}), 500

@canvas_bp.route('/canvas/instructor/courses/<course_id>/quizzes', methods=['GET'])
async def instructor_course_quizzes_route(course_id):
    """Get all quizzes in a course (instructor token required)."""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token', 'message': 'Authorization header with Bearer token is required', 'statusCode': 401}), 401
        token = auth_header.split(' ')[1]
        from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({'error': user_result['error'], 'message': user_result['error'], 'statusCode': user_result['statusCode']}), user_result['statusCode']
        user_id = user_result['user']['id']
        from motor.motor_asyncio import AsyncIOMotorClient
        from config import Config
        client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tlsAllowInvalidCertificates=(Config.ENV == 'development')
        )
        db = client[Config.DATABASE]
        user_doc = await db[Config.ACHIEVEUP_USERS_COLLECTION].find_one({'user_id': user_id})
        if not user_doc or user_doc.get('canvas_token_type', 'student') != 'instructor':
            return jsonify({'error': 'Forbidden', 'message': 'Instructor token required', 'statusCode': 403}), 403
        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({'error': 'No Canvas token', 'message': 'No Canvas API token found for user', 'statusCode': 400}), 400
        from services.achieveup_canvas_service import get_instructor_course_quizzes
        result = await get_instructor_course_quizzes(canvas_token, course_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500}), 500

@canvas_bp.route('/canvas/instructor/courses/<course_id>/quizzes/<quiz_id>/questions', methods=['GET'])
async def instructor_quiz_questions_route(course_id, quiz_id):
    """Get all questions in a quiz (instructor token required)."""
    try:
        #debugging 
        logger.info(f"star route called: /canvas/instructor/courses/{course_id}/quizzes/{quiz_id}/questions")

        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token', 'message': 'Authorization header with Bearer token is required', 'statusCode': 401}), 401
        token = auth_header.split(' ')[1]
        from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return jsonify({'error': user_result['error'], 'message': user_result['error'], 'statusCode': user_result['statusCode']}), user_result['statusCode']
        user_id = user_result['user']['id']
        from motor.motor_asyncio import AsyncIOMotorClient
        from config import Config
        client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tlsAllowInvalidCertificates=(Config.ENV == 'development')
        )
        db = client[Config.DATABASE]
        user_doc = await db[Config.ACHIEVEUP_USERS_COLLECTION].find_one({'user_id': user_id})
        if not user_doc or user_doc.get('canvas_token_type', 'student') != 'instructor':
            return jsonify({'error': 'Forbidden', 'message': 'Instructor token required', 'statusCode': 403}), 403
        
        #course_id = request.args.get('course_id')

        #debugging
        logger.info(f"star course_id from query params: {course_id}")
        if not course_id:
            return jsonify({'error': 'Missing Course_id', 'message': 'course_id query paramenter required', 'statusCode': 400}), 400

        canvas_token = await get_user_canvas_token(user_id)
        if not canvas_token:
            return jsonify({'error': 'No Canvas token', 'message': 'No Canvas API token found for user', 'statusCode': 400}), 400
        from services.achieveup_canvas_service import get_instructor_quiz_questions
        result = await get_instructor_quiz_questions(canvas_token, quiz_id, course_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500}), 500
    
# Test route
@canvas_bp.route('/canvas/test/courses/<course_id>/sync-submissions', methods=['POST'])
async def test_sync_submissions_route(course_id):
    import json
    import time
    from config import Config
    from services.achieveup_auth_service import achieveup_verify_token, get_user_canvas_token
    from services.achieveup_canvas_service import get_instructor_course_quizzes
    from services.canvas_submissions_service import sync_course_submissions_direct, get_all_course_submissions
    from services.mastery_service import mastery_collection
 
    if Config.ENV != 'development':
        return jsonify({'error': 'Test route disabled outside development'}), 404
 
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Missing Bearer token'}), 401
    bearer = auth_header.split(' ')[1]
 
    if request.args.get('raw') == 'true':
        canvas_token = bearer  # Canvas API token passed directly
    else:
        user_result = await achieveup_verify_token(bearer)
        if 'error' in user_result:
            return jsonify(user_result), user_result.get('statusCode', 401)
        canvas_token = await get_user_canvas_token(user_result['user']['id'])
        if not canvas_token:
            return jsonify({'error': 'No stored Canvas token for this user'}), 400
 
    student_id = request.args.get('student_id')
    diagnose = request.args.get('diagnose') == 'true'
 
    def to_json_safe(obj):
        # Converts datetimes/ObjectIds so jsonify can't choke on them.
        return json.loads(json.dumps(obj, default=str))
 
    response: dict = {'course_id': course_id}
 
    try:
        # --- Optional pre-sync diagnostics -------------------------------
        if diagnose:
            quizzes_result = await get_instructor_course_quizzes(canvas_token, course_id)
            if isinstance(quizzes_result, dict) and 'error' in quizzes_result:
                response['diagnose'] = {'error': quizzes_result}
            else:
                quiz_rows = []
                for quiz in quizzes_result or []:
                    subs = await get_all_course_submissions(canvas_token, course_id, quiz.get('id'))
                    quiz_rows.append({
                        'quiz_id': quiz.get('id'),
                        'title': quiz.get('title'),
                        'is_new_quiz': subs.get('is_new_quiz'),  # None until step 3 adds it
                        'submission_count': subs.get('count'),
                        'error': subs.get('error'),
                    })
                response['diagnose'] = {'quizzes': quiz_rows}
 
        # --- The actual sync ---------------------------------------------
        start = time.perf_counter()
        sync_result = await sync_course_submissions_direct(canvas_token, course_id)
        response['elapsed_ms'] = round((time.perf_counter() - start) * 1000)
        response['sync_result'] = sync_result
 
        # --- What the sync wrote -----------------------------------------
        response['mastery_doc_count'] = await mastery_collection.count_documents({'course_id': str(course_id)})
        response['mastery_students'] = len(
            await mastery_collection.distinct('student_id', {'course_id': str(course_id)})
        )
 
        if student_id:
            response['student_mastery'] = await mastery_collection.find(
                {'course_id': str(course_id), 'student_id': str(student_id)},
                {'_id': 0}
            ).to_list(length=None)
 
    except Exception as e:
        logger.exception("sync_course_submissions_direct test route failed")
        response['error'] = {'type': type(e).__name__, 'message': str(e)}
        return jsonify(to_json_safe(response)), 500
 
    status = 500 if isinstance(response.get('sync_result'), dict) and 'error' in response['sync_result'] else 200
    return jsonify(to_json_safe(response)), status
 