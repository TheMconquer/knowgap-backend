# services/badge_service.py

import logging
import uuid
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from services.achieveup_auth_service import achieveup_verify_token
from services.skill_tiers import tier_for_score
from config import Config

from mongodb import get_db

# Set up logging
logger = logging.getLogger(__name__)

# MongoDB setup for AchieveUp badge data (separate from KnowGap)
db = get_db()

achieveup_user_badges_collection = db[Config.ACHIEVEUP_USER_BADGES_COLLECTION]
achieveup_badge_progress_collection = db[Config.ACHIEVEUP_BADGE_PROGRESS_COLLECTION]
achieveup_student_skill_mastery_collection = db[Config.ACHIEVEUP_STUDENT_SKILL_MASTERY_COLLECTION]
achieveup_skill_matrices_collection = db[Config.ACHIEVEUP_SKILL_MATRICES_COLLECTION]
async def generate_badges_for_user(token: str, data: dict) -> dict:
    """Generate badges for a user based on their progress."""
    try:
        # Verify token and get user info
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result
        
        user_id = user_result.get('user_id')
        course_id = data.get('course_id')
        
        if not course_id:
            return {
                'error': 'Missing course_id',
                'message': 'Course ID is required',
                'statusCode': 400
            }
        
        # Get user's skill progress for the course
        progress_data = await get_user_skill_progress(user_id, course_id)
        
        # Generate badges based on progress
        generated_badges = []
        
        for skill_progress in progress_data:
            skill_id = skill_progress.get('skill_id')
            progress_percentage = skill_progress.get('progress_percentage', 0)
            
            skill_name = skill_progress.get('skill_name') or skill_progress.get('skill_id') or 'Skill'
            badge_level = tier_for_score(progress_percentage)
            if badge_level == 'none':
                continue  # No badge for low progress
            badge_name = f"{badge_level.capitalize()} in {skill_name}"
            
            # Check if badge already exists
            existing_badge = await achieveup_user_badges_collection.find_one({
                'user_id': user_id,
                'skill_id': skill_id,
                'badge_level': badge_level,
                'course_id': course_id
            })
            
            if not existing_badge:
                # Create new badge
                badge_id = str(uuid.uuid4())
                badge_doc = {
                    'badge_id': badge_id,
                    'user_id': user_id,
                    'skill_id': skill_id,
                    'skill_name': skill_progress.get('skill_name'),
                    'badge_level': badge_level,
                    'badge_name': badge_name,
                    'course_id': course_id,
                    'progress_percentage': progress_percentage,
                    'earned_at': datetime.now(timezone.utc).replace(tzinfo=None),
                    'shareable_link': f"/badges/{badge_id}/share"
                }
                
                await achieveup_user_badges_collection.insert_one(badge_doc)
                generated_badges.append(badge_doc)
        
        return {
            'message': f'Generated {len(generated_badges)} new badges',
            'badges': generated_badges
        }
        
    except Exception as e:
        logger.error(f"Generate badges error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def get_user_badges(token: str, course_id: str = None, skill_id: str = None) -> dict:
    """Get all badges for the current user."""
    try:
        # Verify token and get user info
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result
        
        user_id = user_result.get('user_id')
        
        # Build query
        query = {'user_id': user_id}
        if course_id:
            query['course_id'] = course_id
        if skill_id:
            query['skill_id'] = skill_id
        
        # Get badges from database
        badges = []
        async for badge in achieveup_user_badges_collection.find(query, {"_id": 0}):
            badges.append(badge)
        
        # Sort by earned date (newest first)
        badges.sort(key=lambda x: x.get('earned_at', datetime.min), reverse=True)
        
        return {'badges': badges}
        
    except Exception as e:
        logger.error(f"Get user badges error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def get_badge_details(token: str, badge_id: str) -> dict:
    """Get detailed information about a specific badge."""
    try:
        # Verify token and get user info
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result
        
        user_id = user_result.get('user_id')
        
        # Find badge in database
        badge = await achieveup_user_badges_collection.find_one({
            'badge_id': badge_id,
            'user_id': user_id
        }, {"_id": 0})
        
        if not badge:
            return {
                'error': 'Badge not found',
                'message': 'Badge not found or access denied',
                'statusCode': 404
            }
        
        # Add additional badge details
        badge_details = {
            **badge,
            'description': f"Earned {badge.get('badge_level', '').title()} level badge for {badge.get('skill_name', 'skill')}",
            'criteria': f"Complete {get_badge_criteria(badge.get('badge_level'))}% of {badge.get('skill_name', 'skill')} assessments",
            'rarity': get_badge_rarity(badge.get('badge_level')),
            'next_level': get_next_badge_level(badge.get('badge_level'))
        }
        
        return {'badge': badge_details}
        
    except Exception as e:
        logger.error(f"Get badge details error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def create_badge_for_student(user_id: str, course_id: str, skill_id: str, badge_level: str, progress_percentage: float, student_name: str = None) -> dict:
    """
    Create a badge for a student. Intended to be called by internal services (e.g. mastery_service).
    """
    try:
        # Get skill details (for name)
        # We try to find name from an existing badge or assignments, or defaults.
        # Ideally we have it. For now, fetch from matrix or mastery?
        # Let's check if the mastery record has it (we stored it in mastery_service)
        mastery_doc = await achieveup_student_skill_mastery_collection.find_one({
            'student_id': user_id, 'course_id': course_id, 'skill_id': skill_id
        })
        # Fallback logic: 1. skill_name in mastery, 2. skill_id (if it's a name), 3. 'Skill'
        skill_name = mastery_doc.get('skill_name') or mastery_doc.get('skill_id') or skill_id or 'Skill'

        # Look up actual course name from skill matrices
        course_name = None
        try:
            matrix = await achieveup_skill_matrices_collection.find_one({
                '$or': [
                    {'course_id': course_id},
                    {'course_id': int(course_id) if str(course_id).isdigit() else course_id}
                ]
            })
            if matrix and matrix.get('matrix_name'):
                course_name = matrix.get('matrix_name')
        except Exception as e:
            logger.warning(f"Could not look up course name for course_id {course_id}: {e}")
        
        if not course_name:
            course_name = 'Unknown Course'

        badge_name = f"{badge_level.title()} in {skill_name}"
        
        badge_id = str(uuid.uuid4())
        badge_doc = {
            'badge_id': badge_id,
            'user_id': user_id,
            'skill_id': skill_id,
            'skill_name': skill_name,
            'badge_level': badge_level,
            'badge_name': badge_name,
            'course_id': course_id,
            'course_name': course_name,
            'student_name': student_name,
            'progress_percentage': progress_percentage,
            'earned_at': datetime.now(timezone.utc).replace(tzinfo=None),
            'shareable_link': f"/badges/{badge_id}/share"
        }
        
        await achieveup_user_badges_collection.insert_one(badge_doc)
        logger.info(f"Awarded {badge_level} badge to {user_id} for {skill_id}")
        return badge_doc
    except Exception as e:
        logger.error(f"Error creating badge for student: {str(e)}")
        return None

async def get_badge_progress(token: str, skill_id: str, course_id: str) -> dict:
    """Get progress toward earning a badge for a specific skill."""
    try:
        # Verify token and get user info
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result
        
        user_id = user_result.get('user_id')
        
        # Get current progress
        progress = await achieveup_badge_progress_collection.find_one({
            'user_id': user_id,
            'skill_id': skill_id,
            'course_id': course_id
        }, {"_id": 0})
        
        if not progress:
            # Initialize progress if not exists
            progress = {
                'user_id': user_id,
                'skill_id': skill_id,
                'course_id': course_id,
                'progress_percentage': 0,
                'questions_attempted': 0,
                'questions_correct': 0,
                'last_updated': datetime.now(timezone.utc).replace(tzinfo=None)
            }
            await achieveup_badge_progress_collection.insert_one(progress)
        
        # Calculate next badge level
        current_level = get_current_badge_level(progress.get('progress_percentage', 0))
        next_level = get_next_badge_level(current_level)
        next_threshold = get_badge_threshold(next_level)
        
        # Get earned badges for this skill
        earned_badges = []
        async for badge in achieveup_user_badges_collection.find({
            'user_id': user_id,
            'skill_id': skill_id,
            'course_id': course_id
        }):
            earned_badges.append({
                'badge_level': badge.get('badge_level'),
                'badge_name': badge.get('badge_name'),
                'earned_at': badge.get('earned_at')
            })
        
        return {
            'progress': progress,
            'current_level': current_level,
            'next_level': next_level,
            'next_threshold': next_threshold,
            'earned_badges': earned_badges,
            'remaining_for_next': max(0, next_threshold - progress.get('progress_percentage', 0))
        }
        
    except Exception as e:
        logger.error(f"Get badge progress error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

# Helper functions
async def get_user_skill_progress(user_id: str, course_id: str) -> list:
    """
    Get user's skill progress for a course based on aggregated mastery data.
    
    Args:
        user_id: Student's Canvas user ID
        course_id: Canvas course ID
        
    Returns:
        list: Skill progress data with percentages
    """
    try:
        # Fetch from new aggregated collection
        cursor = achieveup_student_skill_mastery_collection.find({
            'student_id': user_id,
            'course_id': course_id
        })
        
        progress_data = []
        async for doc in cursor:
            progress_data.append({
                'skill_id': doc.get('skill_id'),
                'skill_name': doc.get('skill_name', 'Unknown Skill'),
                'progress_percentage': round(doc.get('mastery_percentage', 0), 2),
                'questions_attempted': doc.get('total_attempted', 0),
                'questions_correct': doc.get('total_correct', 0)
            })
            
        # If no data found in new collection yet, should we fallback?
        # For now, let's assume the new system is the source of truth.
        # If empty, return empty.
        
        # Sort by skill name
        progress_data.sort(key=lambda x: x['skill_name'])
        
        return progress_data
        
    except Exception as e:
        logger.error(f"Get user skill progress error: {str(e)}")
        return []

def get_badge_criteria(badge_level: str) -> int:
    """Get the criteria percentage for a badge level."""
    criteria_map = {
        'beginner': 25,
        'intermediate': 50,
        'advanced': 75,
        'expert': 90
    }
    return criteria_map.get(badge_level, 0)

def get_badge_rarity(badge_level: str) -> str:
    """Get the rarity of a badge level."""
    rarity_map = {
        'beginner': 'Common',
        'intermediate': 'Uncommon',
        'advanced': 'Rare',
        'expert': 'Legendary'
    }
    return rarity_map.get(badge_level, 'Common')

def get_next_badge_level(current_level: str) -> str:
    """Get the next badge level."""
    level_progression = ['beginner', 'intermediate', 'advanced', 'expert']
    try:
        current_index = level_progression.index(current_level)
        if current_index < len(level_progression) - 1:
            return level_progression[current_index + 1]
    except ValueError:
        pass
    return None

def get_current_badge_level(progress_percentage: float) -> str:
    """Get current badge level based on progress percentage."""
    return tier_for_score(progress_percentage)

def get_badge_threshold(badge_level: str) -> int:
    """Get the threshold percentage for a badge level."""
    threshold_map = {
        'beginner': 25,
        'intermediate': 50,
        'advanced': 75,
        'expert': 90
    }
    return threshold_map.get(badge_level, 0)

async def _instructor_teaches_student(canvas_token: str, student_id: str, hint_course_id: str = None) -> bool:
    """Check real Canvas enrollment for whether this instructor can see the
    student on a course roster. Checks hint_course_id first (one call) since
    callers usually already know which course they're looking at; falls back
    to sweeping the instructor's other courses if that doesn't find a match,
    so a stale/wrong hint degrades gracefully instead of just failing.
    """
    from services.achieveup_canvas_service import get_course_students, get_instructor_courses

    async def course_has_student(course_id: str) -> bool:
        students = await get_course_students(canvas_token, course_id)
        return isinstance(students, list) and any(str(s.get('id')) == str(student_id) for s in students)

    if hint_course_id and await course_has_student(hint_course_id):
        return True

    courses_result = await get_instructor_courses(canvas_token)
    for course in courses_result.get('courses', []):
        course_id = str(course.get('id'))
        if course_id == str(hint_course_id):
            continue  # already checked above
        if await course_has_student(course_id):
            return True

    return False

async def get_student_earned_badges(token: str, student_id: str, course_id: str = None) -> dict:
    """Get all earned badges for a specific student with course information."""
    try:
        # Verify token
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result

        # Self-view always allowed. Otherwise, an instructor may view a
        # student they actually teach (verified against real Canvas
        # enrollment, not just their role) — never any other combination.
        caller = user_result['user']
        from services.achieveup_auth_service import get_user_canvas_token
        canvas_token = await get_user_canvas_token(caller['id'])

        is_self = str(caller.get('canvas_student_id')) == str(student_id)
        is_authorized_instructor = (
            not is_self
            and caller.get('role') == 'instructor'
            and canvas_token
            and await _instructor_teaches_student(canvas_token, student_id, course_id)
        )

        if not is_self and not is_authorized_instructor:
            return {
                'error': 'Forbidden',
                'message': "You do not have access to this student's badges",
                'statusCode': 403
            }

        # Get all badges for the student. A badge document only exists because
        # it already crossed its tier threshold at creation time (see
        # tier_for_score) — no re-filtering by current percentage needed here.
        badges = [badge_doc async for badge_doc in achieveup_user_badges_collection.find({'user_id': student_id}, {"_id": 0})]

        # Get course names from Canvas API
        from services.achieveup_canvas_service import get_instructor_courses

        # Create a map of course_id to course_name
        course_map = {}
        course_ids = list(set(str(b.get('course_id')) for b in badges if b.get('course_id')))
        
        # 1. Try to get course names from skill matrices (fastest and handles mock data)
        for cid in course_ids:
            matrix = await achieveup_skill_matrices_collection.find_one({'$or': [{'course_id': cid}, {'course_id': int(cid) if cid.isdigit() else cid}]})
            if matrix and matrix.get('course_name'):
                course_map[cid] = matrix.get('course_name')

        # 2. Try canvas API for missing names
        missing_ids = [cid for cid in course_ids if cid not in course_map]
        if missing_ids and canvas_token:
            try:
                courses_result = await get_instructor_courses(canvas_token)
                if 'courses' in courses_result:
                    for course in courses_result['courses']:
                        cid_str = str(course.get('id'))
                        if cid_str in missing_ids:
                            course_map[cid_str] = course.get('name', 'Unknown Course')
            except Exception as e:
                logger.error(f"Error fetching courses: {str(e)}")
        
        # Enrich badges with course names
        enriched_badges = []
        for badge in badges:
            # Robust name fallback for existing data
            b_name = badge.get('badge_name')
            s_name = badge.get('skill_name') or badge.get('skill_id') or 'Skill'
            level = badge.get('badge_level', 'Skill').title()
            
            if not b_name or b_name == 'Skill' or 'in Skill' in b_name:
                b_name = f"{level} in {s_name}"

            enriched_badge = {
                'badge_id': badge.get('badge_id'),
                'badge_name': b_name,
                'skill_name': s_name,
                'badge_level': badge.get('badge_level'),
                'progress_percentage': badge.get('progress_percentage'),
                'earned_at': badge.get('earned_at'),
                'course_id': badge.get('course_id'),
                'course_name': course_map.get(str(badge.get('course_id'))) or badge.get('course_name') or 'Unknown Course'
            }
            enriched_badges.append(enriched_badge)
        
        # Sort by earned date (newest first)
        enriched_badges.sort(key=lambda x: x.get('earned_at', datetime.min), reverse=True)
        
        # Get student name from first badge if available
        student_name = next((b.get('student_name') for b in badges if b.get('student_name')), None)
        
        return {
            'student_id': student_id,
            'student_name': student_name,
            'total_badges': len(enriched_badges),
            'badges': enriched_badges
        }
        
    except Exception as e:
        logger.error(f"Get student earned badges error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500} 

async def _resolve_public_student_name(badges: list, student_id: str) -> str:
    """Best-effort display name for a public badge profile.

    Tries the name cached on an existing badge first (fast, no network call).
    Falls back to a live Canvas roster lookup, scoped to the instructor who
    actually owns each course (via that course's skill matrix created_by)
    -- never an arbitrary instructor's token. If no source has a name,
    returns None; callers must show "unknown", never substitute a fake one.
    """
    cached_name = next((b.get('student_name') for b in badges if b.get('student_name')), None)
    if cached_name:
        return cached_name

    try:
        from services.achieveup_auth_service import get_user_canvas_token
        from services.achieveup_canvas_service import get_course_students

        course_ids = {str(b.get('course_id')) for b in badges if b.get('course_id')}
        for course_id in course_ids:
            matrix = await achieveup_skill_matrices_collection.find_one({
                '$or': [
                    {'course_id': course_id},
                    {'course_id': int(course_id) if course_id.isdigit() else course_id}
                ]
            })
            instructor_id = matrix.get('created_by') if matrix else None
            if not instructor_id:
                continue

            canvas_token = await get_user_canvas_token(instructor_id)
            if not canvas_token:
                continue

            students = await get_course_students(canvas_token, course_id)
            if not isinstance(students, list):
                continue
            match = next((s for s in students if str(s.get('id')) == str(student_id)), None)
            if match:
                return match.get('name') or match.get('sortable_name')
    except Exception as e:
        logger.warning(f"Could not resolve display name for student {student_id}: {e}")

    return None


def _public_badge_view(badge: dict) -> dict:
    """Reshape a raw badge document into the subset safe to expose publicly."""
    skill_name = badge.get('skill_name')
    if not skill_name or skill_name == 'Skill':
        skill_name = badge.get('skill_id') or 'Skill'

    level = (badge.get('badge_level') or 'Skill').title()
    badge_name = badge.get('badge_name')
    if not badge_name or badge_name == 'Skill' or 'in Skill' in badge_name:
        badge_name = f"{level} in {skill_name}"

    return {
        'badge_id': badge.get('badge_id'),
        'badge_name': badge_name,
        'skill_name': skill_name,
        'badge_level': badge.get('badge_level'),
        'progress_percentage': badge.get('progress_percentage'),
        'earned_at': badge.get('earned_at'),
        'course_id': badge.get('course_id'),
        'course_name': badge.get('course_name')
    }


async def get_public_badges_by_share(share_id: str) -> dict:
    """Get all earned badges for whichever student owns this share link."""
    try:
        users_collection = db[Config.ACHIEVEUP_USERS_COLLECTION]

        # Resolve the student from the share token — never from client input.
        # An unknown share_id, or one cleared by opting out, simply won't
        # match here. badge_sharing_opted_out is checked explicitly too as a
        # second guard, in case a share_id is ever left behind opted-out.
        share_owner = await users_collection.find_one({
            'badge_share_id': share_id,
            'badge_sharing_opted_out': {'$ne': True}
        })
        if not share_owner:
            return {
                'error': 'Not found',
                'message': 'This share link is invalid or no longer active',
                'statusCode': 404
            }

        # share_owner['user_id'] is this app's own internal account id, not
        # the Canvas id badge documents are keyed by — using it here would
        # silently match zero badges for every real student.
        student_id = share_owner['canvas_student_id']

        # Get all badges for the student. A badge document only exists because
        # it already crossed its tier threshold at creation time (see
        # tier_for_score) — no re-filtering by current percentage needed here.
        badges = [
            b async for b in
            achieveup_user_badges_collection.find({'user_id': student_id}, {"_id": 0})
        ]
        badges.sort(key=lambda b: b.get('earned_at', datetime.min), reverse=True)

        student_name = await _resolve_public_student_name(badges, student_id)

        return {
            'student_name': student_name,
            'total_badges': len(badges),
            'badges': [_public_badge_view(b) for b in badges]
        }

    except Exception as e:
        logger.error(f"Get public badges by share error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def generate_badge_share_link(token: str, student_id: str = None, course_id: str = None) -> dict:
    """Get (or lazily create) a student's public badge share link.

    With no student_id, this is self-service: a student gets their own link.
    With a student_id for someone else, the caller must be an instructor who
    actually teaches that student (verified against real Canvas enrollment —
    course_id is just a cheap first guess at which course to check, falling
    back to a full sweep). Either way, sharing must not be opted out.
    """
    try:
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result

        caller = user_result['user']
        target_student_id = student_id or caller.get('canvas_student_id')
        is_self = str(target_student_id) == str(caller.get('canvas_student_id'))

        if not is_self:
            if caller.get('role') != 'instructor':
                return {
                    'error': 'Forbidden',
                    'message': "You do not have access to this student's badges",
                    'statusCode': 403
                }
            from services.achieveup_auth_service import get_user_canvas_token
            canvas_token = await get_user_canvas_token(caller['id'])
            if not canvas_token or not await _instructor_teaches_student(canvas_token, target_student_id, course_id):
                return {
                    'error': 'Forbidden',
                    'message': "You do not have access to this student's badges",
                    'statusCode': 403
                }

        users_collection = db[Config.ACHIEVEUP_USERS_COLLECTION]
        target_doc = await users_collection.find_one({'canvas_student_id': target_student_id})
        if not target_doc:
            return {'error': 'Not found', 'message': 'Student not found', 'statusCode': 404}

        if target_doc.get('badge_sharing_opted_out'):
            return {
                'error': 'Forbidden',
                'message': 'This student has opted out of badge sharing',
                'statusCode': 403
            }

        # Reuse an existing share_id so re-sharing doesn't invalidate a link
        # that's already been handed out.
        share_id = target_doc.get('badge_share_id') or str(uuid.uuid4())
        share_link = f"https://achieveup.ucf.edu/badges/share/{share_id}"

        await users_collection.update_one(
            {'canvas_student_id': target_student_id},
            {'$set': {
                'badge_share_id': share_id,
                'badge_shared_at': datetime.now(timezone.utc).replace(tzinfo=None)
            }}
        )

        return {
            'message': 'Badge profile shared successfully',
            'share_link': share_link,
            'share_id': share_id
        }

    except Exception as e:
        logger.error(f"Generate badge share link error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def set_badge_sharing_opt_out(token: str, opted_out: bool) -> dict:
    """Set the caller's own badge-sharing preference (Settings page only —
    this is self-service, never on behalf of another student).

    Opting out always kills any existing share_id outright, rather than just
    flagging it, so a link already handed out can't silently come back to
    life if the student opts back in later — re-sharing mints a fresh one.
    """
    try:
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result

        user_id = user_result['user']['id']
        users_collection = db[Config.ACHIEVEUP_USERS_COLLECTION]

        update = {'$set': {'badge_sharing_opted_out': opted_out}}
        if opted_out:
            update['$unset'] = {'badge_share_id': ''}
        await users_collection.update_one({'user_id': user_id}, update)

        return {
            'message': 'Badge sharing turned off' if opted_out else 'Badge sharing turned on',
            'opted_out': opted_out
        }

    except Exception as e:
        logger.error(f"Set badge sharing opt-out error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}

async def get_badge_share_status(token: str) -> dict:
    """Check the caller's own current badge-sharing link and opt-out preference.

    Read-only by design: unlike generate_badge_share_link, this must never
    create a link or change the opt-out preference as a side effect of just
    checking status.
    """
    try:
        user_result = await achieveup_verify_token(token)
        if 'error' in user_result:
            return user_result

        user_id = user_result['user']['id']

        users_collection = db[Config.ACHIEVEUP_USERS_COLLECTION]
        user_doc = await users_collection.find_one({'user_id': user_id}) or {}

        share_id = user_doc.get('badge_share_id')
        opted_out = bool(user_doc.get('badge_sharing_opted_out'))

        return {
            'shared': bool(share_id) and not opted_out,
            'share_link': f"https://achieveup.ucf.edu/badges/share/{share_id}" if share_id and not opted_out else None,
            'opted_out': opted_out
        }

    except Exception as e:
        logger.error(f"Get badge share status error: {str(e)}")
        return {'error': 'Internal server error', 'statusCode': 500}