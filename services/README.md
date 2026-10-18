# Services Directory

## Overview

The `services` directory is responsible for handling the core business logic and database interactions of the KnowGap Backend API. Each service file is organized around specific features or resources, separating core logic from routing. This modular approach enables easier maintenance, testing, and scalability.

## Service Files

### 1. `video_service.py`
- **Purpose**: Manages all operations related to KnowGap videos, such as generating recommendations, updating video metadata, and student votes.
- **Key Functions**:
  - **`get_assessment_videos(student_id, course_id)`**: Retrieves recommended videos for every quiz the student has submissions for.
  - **`get_course_videos(course_id)`**: Fetches video data for a specific course.
  - **`update_videos_for_filter(filter_criteria)`**: Updates video recommendations for questions that match a given filter.
  - **`update_course_videos(course_id)`**: Updates all video recommendations for questions within a specified course.
  - **`update_video_link(...)`**, **`add_video(...)`**, **`remove_video(...)`**: Edit the videos attached to a question.
  - **`vote_video(...)`**, **`get_vote_counts(...)`**, **`get_student_votes(...)`**: Record and read student votes on recommended videos.

### 2. `course_service.py`
- **Purpose**: Handles KnowGap course data, including the course context, risk toggle, and syncing quiz and student data from Canvas.
- **Key Functions**:
  - **`update_context(course_id, course_context)`**: Updates or inserts context information for a specific course, aiding in more targeted video recommendations.
  - **`update_course_risk_toggle(course_id, toggle_risk)`** / **`get_course_risk_toggle(course_id)`**: Set and read whether risk prediction is shown for a course.
  - **`update_student_quiz_data(...)`**, **`update_quiz_questions_per_course(...)`**, **`sync_all_quizzes_questions(...)`**: Pull quiz, question, and student submission data from Canvas into the database.
  - **`get_student_grade(...)`** / **`get_student_profile(...)`**: Fetch a student's grade and Canvas profile.

### 3. `user_service.py`
- **Purpose**: Manages KnowGap extension user tokens. This service ensures that user tokens are securely encrypted before they are stored.
- **Key Functions**:
  - **`add_user(user_id, access_token, course_ids, link)`**: Encrypts and stores or updates the user token, linking it to specified courses.

### 4. `support_service.py`
- **Purpose**: Finds support videos for students based on their risk level.
- **Key Functions**:
  - **`get_videos_for_risk_level(risk_level)`**: Searches YouTube for videos matching a `low`, `medium`, or `high` risk level.
  - **`get_random_video(videos)`**: Picks one video from the results.

### 5. `achieveup_auth_service.py`
- **Purpose**: AchieveUp account management: signup, login, JWT creation and verification, profile updates, and password changes.
- **Key Functions**:
  - **`achieveup_signup(...)`** / **`achieveup_login(email, password)`**: Create an account or log in, returning a JWT.
  - **`achieveup_verify_token(token)`**: Verifies a JWT and returns the user. Used by almost every AchieveUp service.
  - **`get_user_canvas_token(user_id)`**: Returns a user's decrypted Canvas API token.

### 6. `achieveup_canvas_service.py`
- **Purpose**: All Canvas API access for AchieveUp, including token validation and fetching courses, quizzes, questions, and students (Classic Quizzes and New Quizzes).
- **Key Functions**:
  - **`validate_canvas_token(...)`** / **`validate_canvas_instructor_for_course(...)`**: Validate a Canvas token, or that it belongs to an instructor of a course.
  - **`get_instructor_courses(...)`**, **`get_instructor_course_quizzes(...)`**, **`get_instructor_quiz_questions(...)`**, **`get_course_students(...)`**: Instructor views of Canvas data.
  - **`is_new_quiz(...)`**: Checks whether a quiz is a New Quiz or a Classic Quiz.

### 7. `achieveup_service.py`
- **Purpose**: Core AchieveUp logic: skill matrices, course descriptions, skill assignment, student progress, instructor dashboard, AI analysis, and importing setups between courses.
- **Key Functions**:
  - **`create_skill_matrix(...)`**, **`update_skill_matrix(...)`**, **`get_all_skill_matrices_by_course(...)`**, **`delete_skill_matrix(...)`**: Manage skill matrices.
  - **`assign_skills_to_questions(...)`** / **`get_assigned_skills(...)`**: Map quiz questions to skills.
  - **`suggest_course_skills_ai(...)`** / **`analyze_questions_with_ai(...)`**: AI skill suggestions for a course and for quiz questions.
  - **`import_matrices_from_course(...)`** / **`import_skill_setting_from_course(...)`**: Copy matrices and skill assignments from one course to another.

### 8. `achieveup_ai_service.py`
- **Purpose**: Uses OpenAI to classify quiz questions into the skills of a skill matrix.
- **Key Functions**:
  - **`analyze_questions(questions_data, course_skills)`**: Analyzes questions in batches and returns suggested skills for each.

### 9. `canvas_submissions_service.py`
- **Purpose**: Fetches student quiz submissions from Canvas and turns them into skill mastery data.
- **Key Functions**:
  - **`sync_course_submissions_direct(canvas_token, course_id)`**: Syncs all quiz submissions for a course and updates student mastery. Used by the background sync and the instructor force-sync.

### 10. `mastery_service.py`
- **Purpose**: Calculates each student's mastery of each skill from their quiz submissions and awards badges.
- **Key Functions**:
  - **`update_student_mastery(submission_data)`**: Updates skill mastery from a processed submission.
  - **`check_and_award_badge(...)`**: Awards a badge when a student reaches a new tier.

### 11. `badge_service.py`
- **Purpose**: Creates badges and manages earned badges and public badge sharing.
- **Key Functions**:
  - **`create_badge_for_student(...)`**: Creates a badge for a student (called by `mastery_service.py`).
  - **`get_student_earned_badges(...)`**: Gets a student's earned badges with course information.
  - **`generate_badge_share_link(...)`**, **`get_public_badges_by_share(share_id)`**, **`set_badge_sharing_opt_out(...)`**, **`get_badge_share_status(...)`**: Public badge share links and the opt-out setting.

### 12. `skill_tiers.py`
- **Purpose**: Single source of truth for the mastery percentage needed for each badge tier.
- **Key Functions**:
  - **`tier_for_score(percentage)`**: Maps a 0-100 mastery percentage to a tier label.
  - **`is_mastered(percentage)`**: Whether a percentage counts as mastered.

### 13. `analytics_service.py`
- **Purpose**: Instructor analytics for AchieveUp.
- **Key Functions**:
  - **`get_course_students_analytics(token, course_id)`**: Per-student skill breakdown, risk level, and earned badges for a course.

### 14. `skill_video_service.py`
- **Purpose**: Manages the curated list of videos for each AchieveUp skill.
- **Key Functions**:
  - **`get_skill_videos(...)`**, **`add_skill_video(...)`**, **`update_skill_video(...)`**, **`remove_skill_video(...)`**: Manage a skill's video list.
  - **`generate_ai_video_recommendations(...)`** / **`verify_ai_video(...)`**: Add AI-suggested videos and mark them as reviewed.
  - **`find_relevant_moment(token, video_id)`**: Uses the video transcript to find the moment most relevant to the skill.
  - **`vote_skill_video(...)`**: Records whether a student found a video helpful.

## Async Flow and Database Interactions

All service functions utilize asynchronous programming with the `Motor` library for MongoDB interactions, which ensures non-blocking data access and efficient performance when working with the database.

- **MongoDB Collections**: Each service gets the shared database connection from `mongodb.py` (`get_db()`) and interacts with specific collections whose names are defined in `config.py`, such as `Quiz Questions`, `Tokens`, and `Course Contexts` for KnowGap, and the `AchieveUp_*` collections for AchieveUp.
- **Data Validation and Error Handling**: Each function performs data validation and error handling to ensure reliable database interactions and minimize the risk of runtime issues.

## How Services are Used in Routes

The service functions are called by route handlers in the `routes` directory. This approach:
1. Keeps routes clean and focused on request-response handling.
2. Allows services to manage complex business logic and database interactions.

Example usage in a route:
```python
# Example from video_routes.py
from services.video_service import get_course_videos

@app.route('/get-course-videos', methods=['POST', 'OPTIONS'])
async def get_course_videos_route():
    data = await request.get_json()
    course_videos = await get_course_videos(data.get("course_id"))
    return jsonify({"course_videos": course_videos}), 200
```

## Adding New Services

To add a new service:

1. **Create a new file** in the `services` directory with a descriptive name (e.g., `new_feature_service.py`).
2. **Define the necessary business logic and database functions** within this file.
3. **Import and call these functions** within the appropriate route file to make them accessible via the API.

## Benefits of Service Layer

- **Separation of Concerns**: By keeping business logic separate from route handlers, the codebase remains modular and easier to manage.
- **Reusability**: Service functions can be reused across different routes or even other services as needed.
- **Testability**: Each service function can be individually tested, making it easier to maintain and scale the application.

