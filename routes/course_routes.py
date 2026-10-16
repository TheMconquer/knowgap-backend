from quart import request, jsonify
from services.achieveup_canvas_service import validate_canvas_instructor_for_course
from services.course_service import update_context,update_course_risk_toggle, get_course_risk_toggle, update_student_quiz_data, get_student_grade, get_student_profile, sync_all_quizzes_questions
from services.video_service import update_course_videos

def init_course_routes(app):
    @app.route('/update-course-context', methods=['POST'])
    async def update_course_context_route():
        """Route to update course context and trigger video updates."""
        data = await request.get_json()
        course_id = data.get('course_id')
        course_context = data.get('course_context')

        # Log the request data for debugging
        print(f"Received data for course context update: {data}")

        # Validate required fields
        if not course_id or not course_context:
            return jsonify({'error': 'Missing course_id or course_context'}), 400

        # Attempt to update the course context
        context_result = await update_context(course_id, course_context)
        print(f"Context update result: {context_result}")

        # Handle context update response based on the status
        if context_result['status'] == 'Success':
            # Trigger video updates if context update is successful
            videos_result = await update_course_videos(course_id)
            return jsonify({
                'context_update': context_result,
                'videos_update': videos_result
            }), 200
        elif context_result['status'] == 'No changes made':
            return jsonify({
                'status': 'No changes made',
                'message': context_result['message']
            }), 200
        else:
            return jsonify({
                'status': 'Error',
                'message': 'Unexpected result from update operation',
                'error': context_result
            }), 500

    @app.route('/update-course-db', methods=['POST'])
    async def update_course_db_route():
        """Route to update database with course quiz information and student data."""
        data = await request.get_json()
        course_id = data.get('course_id')
        access_token = data.get('access_token')
        link = data.get('link')
        student_id = data.get('student_id')

        print(f"Received data for course DB update: {data}")

        # Validate required fields
        if not course_id or not access_token or not link:
            print("Missing required fields in request")
            return jsonify({'error': 'Missing course_id, access_token, or link'}), 400

        print(f"Starting database update for course {course_id}")
        # Attempt to update the course database
        if student_id:
            db_result = await update_student_quiz_data(course_id, access_token, link, student_id)
        else:
            db_result = await update_student_quiz_data(course_id, access_token, link)
            # After updating student quiz data, ensure all quizzes/questions are in the DB (instructor mode)
            #await update_quiz_questions_per_course(course_id, access_token, link)
        print(f"Database update result: {db_result}")

        if db_result.get('status') == 'Error':
            print(f"Error updating database: {db_result.get('error')}")
            return jsonify({'status': 'Error', 'message': db_result.get('error')}), 500

        '''
        print(f"Starting quiz questions update for course {course_id}")
        # Update quiz questions
        quiz_result = await update_quiz_questions_per_course(course_id, access_token, link)
        print(f"Quiz update result: {quiz_result}")

        if quiz_result.get('status') == 'Success':
            print(f"Successfully updated course {course_id}")
            return jsonify({'status': 'Success', 'message': 'Course database updated successfully'}), 200
        else:
            print(f"Failed to update quiz questions for course {course_id}")
            return jsonify({'status': 'Error', 'message': quiz_result.get('error', 'Failed to update quiz questions')}), 500
        '''
        return jsonify({'status': 'Success', 'message': 'Course database updated successfully'}), 200

    @app.route('/get-student-grade', methods=['POST'])
    async def get_student_grade_route():
        """Route to fetch a student's grade for a course."""
        data = await request.get_json()
        course_id = data.get('course_id')
        user_id = data.get('user_id')
        access_token = data.get('access_token')
        canvas_domain = data.get('canvas_domain')

        if not course_id or not user_id or not access_token or not canvas_domain:
            return jsonify({'error': 'Missing course_id, user_id, access_token, or canvas_domain'}), 400

        grade = await get_student_grade(course_id, user_id, access_token, canvas_domain)
        if grade is None:
            return jsonify({'status': 'Error', 'message': 'Could not fetch grade'}), 500
        return jsonify({'status': 'Success', 'grade': grade}), 200
    
    @app.route('/get-toggle-risk/<course_id>', methods=['GET'])
    async def get_toggle_risk_route(course_id):
        """Get the risk toggle for course"""
        try:
            result = await get_course_risk_toggle(course_id)
        
            if 'status' in result and result['status'] == 'Error':
                print(f"Error getting toggle risk: {result}")
                return jsonify(result), 500
        
            return jsonify(result), 200
        
        except Exception as e:
            print(f"Error in get toggle risk route: {str(e)}")
            return jsonify({'status': 'Error', 'message': str(e)}), 500
        
    @app.route('/update-toggle-risk/<course_id>', methods=['POST'])
    async def update_toggle_risk_route(course_id):
        try:

            #authentication
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                return jsonify({
                    'error': 'Missing token',
                    'message': 'Authorization header with Bearer token is required',
                    'statusCode': 401
                }), 401
            canvas_api_token = auth_header.split(' ')[1]
            
            if not canvas_api_token:
                return jsonify({
                    'error': 'Invalid token',
                    'message': 'Token is missing',
                    'statusCode': 401
                }), 401

            #canvas_token_type = 'instructor'
            validation_result = await validate_canvas_instructor_for_course(canvas_api_token, course_id) 

            if not validation_result['valid']:
                return jsonify({
                    'error': 'Invalid Canvas Token',
                    'message': validation_result['message'],
                    'statusCode': 400 
                }),400

            print("Token validated - User is confirmed instructor")

            #getting the new value of risk toggle that we want to update too
            data = await request.get_json()
            print(f"Received data for toggle risk update: {data}")
        
            if not data or 'toggle_risk' not in data:
                return jsonify({'error': 'Invalid request', 'message': 'toggle_risk field is required', 'statusCode': 400}), 400

            #updates risk toggle
            result = await update_course_risk_toggle(course_id, data['toggle_risk'])
        
            print(f"Toggle risk update result: {result}")
        
            if result['status'] == 'Error':
                return jsonify(result), 500
        
            return jsonify(result), 200
        
        except Exception as e:
            print(f"Error in update toggle risk route: {str(e)}")
            return jsonify({
                'error': 'Internal server error', 'message': 'An unexpected error occurred', 'statusCode': 500
            }), 500

    @app.route('/get-student-profile', methods=['POST'])
    async def get_student_profile_route():
        """Route to fetch the Canvas user profile for the current user."""
        data = await request.get_json()
        access_token = data.get('access_token')
        canvas_domain = data.get('canvas_domain')

        if not access_token or not canvas_domain:
            return jsonify({'error': 'Missing access_token or canvas_domain'}), 400

        profile = await get_student_profile(access_token, canvas_domain)
        if profile is None:
            return jsonify({'status': 'Error', 'message': 'Could not fetch user profile'}), 500
        return jsonify({'status': 'Success', 'profile': profile}), 200

    @app.route('/sync-all-quizzes-questions', methods=['POST'])
    async def sync_all_quizzes_questions_route():
        """Route to perform a deep sync of all quizzes and questions for a course (instructor-only, heavy operation).
        Accepts course_id, access_token, and link in the JSON body."""
        data = await request.get_json()
        course_id = data.get('course_id')
        access_token = data.get('access_token')
        link = data.get('link')
        if not course_id or not access_token or not link:
            return jsonify({'error': 'Missing course_id, access_token, or link'}), 400
        result = await sync_all_quizzes_questions(course_id, access_token, link)
        return jsonify(result), 200