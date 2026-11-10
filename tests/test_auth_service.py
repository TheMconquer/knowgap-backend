import functools
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import bcrypt
import jwt
from pymongo.errors import DuplicateKeyError

from config import Config
from services import achieveup_auth_service as auth_service
from services.achieveup_auth_service import JWT_ALGORITHM, JWT_SECRET
from utils.encryption_utils import decrypt_token, encrypt_token

KEY = bytes.fromhex(Config.HEX_ENCRYPTION_KEY)
CANVAS_TOKEN = 'c' * 64
PASSWORD = 'Passw0rd!'
#Real bcrypt but at  lowest cost so the tests stay fast
FAST_SALT = functools.partial(bcrypt.gensalt, rounds=4)

STUDENT_OK = {'valid': True, 'message': 'Token is valid', 'user_info': {'id': 555}}
INSTRUCTOR_OK = {'valid': True, 'message': 'Token is valid', 'user_info': {'id': 900}}
NOT_VALID = {'valid': False, 'message': 'Invalid Canvas API token. Please check your token and try again.'}


def naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeUsers:
    #In memory stand in for the users collection
    def __init__(self, docs=()):
        self.docs = [dict(doc) for doc in docs]
        self.insert_error = None

    def _match(self, query):
        return next((doc for doc in self.docs
                     if all(doc.get(key) == value for key, value in query.items())), None)

    async def find_one(self, query):
        doc = self._match(query)
        return dict(doc) if doc else None

    async def insert_one(self, doc):
        if self.insert_error:
            raise self.insert_error
        self.docs.append(dict(doc))

    async def update_one(self, query, update):
        doc = self._match(query)
        if doc:
            doc.update(update.get('$set', {}))
            for key in update.get('$unset', {}):
                doc.pop(key, None)


def user_doc(**overrides):
    return {
        'user_id': 'u1', 'name': 'Ann', 'email': 'ann@ucf.edu',
        'password': bcrypt.hashpw(PASSWORD.encode(), FAST_SALT()).decode(),
        'role': 'student', 'canvas_token_type': 'student', 'canvas_student_id': 555,
        'canvas_api_token': encrypt_token(KEY, CANVAS_TOKEN), **overrides,
    }


def token_for(user_id='u1', **claims):
    payload = {'user_id': user_id, 'email': 'ann@ucf.edu', 'role': 'student',
               'exp': datetime.now(timezone.utc) + timedelta(hours=1), **claims}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


class AuthServiceTestCase(unittest.IsolatedAsyncioTestCase):
    #Uses a fake users collection, a fake Canvas, and cheap bcrypt rounds

    def setUp(self):
        self.users = FakeUsers()
        self.canvas_answers = {'instructor': NOT_VALID, 'student': STUDENT_OK}

        async def validate(canvas_token, canvas_token_type='student'):
            return self.canvas_answers[canvas_token_type]
        self.validate = AsyncMock(side_effect=validate)
        self.expiration = AsyncMock(return_value=(200, None))

        for target, value in [
            ('services.achieveup_auth_service.achieveup_users_collection', self.users),
            ('services.achieveup_canvas_service.validate_canvas_token', self.validate),
            ('services.achieveup_canvas_service.get_token_expiration', self.expiration),
            ('bcrypt.gensalt', FAST_SALT),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class PasswordRulesTest(unittest.TestCase):
    def test_each_rule_has_its_own_message(self):
        for password, message in [
            ('Sh0rt', 'Password must be at least 8 characters.'),
            ('nocapital1', 'Password must contain at least one capital letter.'),
            ('NOLOWER123', 'Password must contain at least one lowercase letter.'),
            ('NoNumbersHere', 'Password must contain at least one number.'),
        ]:
            with self.subTest(password=password):
                self.assertEqual(auth_service.password_meets_requirements(password), message)

    def test_good_password_passes(self):
        for password in ('Passw0rd', 'aB3aB3aB3', 'Long Pass Phrase 9'):
            with self.subTest(password=password):
                self.assertIsNone(auth_service.password_meets_requirements(password))


class JwtTokenTest(AuthServiceTestCase):
    def test_token_carries_identity_and_expires_in_a_day(self):
        token = auth_service.create_jwt_token('u1', 'ann@ucf.edu', 'instructor', 'instructor')
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        self.assertEqual((payload['user_id'], payload['email'], payload['role'], payload['canvas_token_type']),
                         ('u1', 'ann@ucf.edu', 'instructor', 'instructor'))
        self.assertEqual(payload['exp'] - payload['iat'], 24 * 3600)

    async def test_valid_token_returns_user_without_secrets(self):
        self.users.docs.append(user_doc())
        result = await auth_service.achieveup_verify_token(token_for())
        self.assertEqual(result['user']['id'], 'u1')
        self.assertTrue(result['user']['hasCanvasToken'])
        self.assertEqual(result['user']['canvas_student_id'], 555)
        self.assertNotIn('password', result['user'])
        self.assertNotIn('canvas_api_token', result['user'])

    async def test_expired_token(self):
        self.users.docs.append(user_doc())
        result = await auth_service.achieveup_verify_token(
            token_for(exp=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertEqual((result['error'], result['statusCode']), ('Token expired', 401))

    async def test_token_signed_with_another_secret(self):
        self.users.docs.append(user_doc())
        forged = jwt.encode({'user_id': 'u1', 'email': 'a', 'role': 'instructor'}, 'a-different-secret-that-is-long-enough',
                            algorithm='HS256')
        result = await auth_service.achieveup_verify_token(forged)
        self.assertEqual((result['error'], result['statusCode']), ('Invalid token', 401))

    async def test_unsigned_token_is_rejected(self):
        self.users.docs.append(user_doc())
        unsigned = jwt.encode({'user_id': 'u1', 'email': 'a', 'role': 'instructor'}, None, algorithm='none')
        result = await auth_service.achieveup_verify_token(unsigned)
        self.assertEqual(result['statusCode'], 401)

    async def test_token_missing_a_claim_is_rejected(self):
        self.users.docs.append(user_doc())
        for missing in ('user_id', 'email', 'role'):
            with self.subTest(missing=missing):
                payload = {'user_id': 'u1', 'email': 'a', 'role': 'student'}
                del payload[missing]
                result = await auth_service.achieveup_verify_token(jwt.encode(payload, JWT_SECRET, algorithm='HS256'))
                self.assertEqual(result['statusCode'], 401)

    async def test_deleted_user(self):
        result = await auth_service.achieveup_verify_token(token_for('gone'))
        self.assertEqual(result['statusCode'], 404)

    async def test_role_comes_from_the_database_not_the_token(self):
        self.users.docs.append(user_doc(role='student'))
        result = await auth_service.achieveup_verify_token(token_for(role='instructor'))
        self.assertEqual(result['user']['role'], 'student')

    async def test_canvas_token_status(self):
        now = naive_utc_now()
        for expires, status in [
            (None, 'Valid'),
            (now - timedelta(minutes=1), 'Expired'),
            (now + timedelta(days=3), 'Expiring Soon'),
            (now + timedelta(days=30), 'Valid'),
        ]:
            with self.subTest(expires=expires):
                self.users.docs = [user_doc(canvas_token_expiration=expires)]
                user = (await auth_service.achieveup_verify_token(token_for()))['user']
                self.assertEqual(user['canvasTokenStatus'], status)
                self.assertEqual(user['canvasTokenExpiresAt'], expires.isoformat() if expires else None)

    async def test_database_error_is_a_generic_500(self):
        with patch.object(self.users, 'find_one', AsyncMock(side_effect=Exception('db down'))), \
             self.assertLogs('services.achieveup_auth_service', 'ERROR'):
            result = await auth_service.achieveup_verify_token(token_for())
        self.assertEqual(result, {'error': 'Internal server error', 'statusCode': 500})


class SignupTest(AuthServiceTestCase):
    async def signup(self, email='ann@ucf.edu', password=PASSWORD, canvas_token=CANVAS_TOKEN, token_type=None):
        return await auth_service.achieveup_signup('Ann', email, password, canvas_token, token_type)

    async def test_student_signup(self):
        result = await self.signup()
        self.assertEqual(result['user']['role'], 'student')
        self.assertEqual(result['user']['canvasTokenType'], 'student')
        self.assertEqual(result['user']['canvas_student_id'], 555)
        self.assertFalse(result['user']['has_student_access'])
        payload = jwt.decode(result['token'], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        self.assertEqual(payload['user_id'], result['user']['id'])
        self.assertEqual(payload['role'], 'student')

    async def test_instructor_is_detected_from_canvas(self):
        self.canvas_answers['instructor'] = INSTRUCTOR_OK
        result = await self.signup()
        self.assertEqual(result['user']['role'], 'instructor')
        self.assertEqual(result['user']['canvas_student_id'], 900)
        self.assertTrue(result['user']['has_student_access'])

    async def test_instructor_without_student_enrollments(self):
        self.canvas_answers = {'instructor': INSTRUCTOR_OK, 'student': NOT_VALID}
        result = await self.signup()
        self.assertEqual(result['user']['role'], 'instructor')
        self.assertFalse(result['user']['has_student_access'])

    async def test_client_cannot_claim_instructor(self):
        result = await self.signup(token_type='instructor')
        self.assertEqual(result['user']['role'], 'student')
        self.assertEqual(self.users.docs[0]['role'], 'student')

    async def test_invalid_canvas_token_creates_nothing(self):
        self.canvas_answers['student'] = NOT_VALID
        result = await self.signup()
        self.assertEqual(result['statusCode'], 400)
        self.assertEqual(result['message'], NOT_VALID['message'])
        self.assertEqual(self.users.docs, [])

    async def test_short_canvas_token_is_rejected_before_canvas(self):
        result = await self.signup(canvas_token='x' * 63)
        self.assertEqual(result['statusCode'], 400)
        self.validate.assert_not_awaited()
        self.assertEqual(self.users.docs, [])

    async def test_canvas_token_is_required(self):
        for canvas_token in (None, ''):
            with self.subTest(canvas_token=canvas_token):
                result = await self.signup(canvas_token=canvas_token)
                self.assertEqual(result['error'], 'Canvas API token required')
        self.validate.assert_not_awaited()

    async def test_weak_password_is_rejected(self):
        result = await self.signup(password='password')
        self.assertEqual(result['statusCode'], 400)
        self.assertEqual(result['message'], 'Password must contain at least one capital letter.')
        self.validate.assert_not_awaited()

    async def test_existing_email(self):
        self.users.docs.append(user_doc())
        result = await self.signup()
        self.assertEqual(result['statusCode'], 409)
        self.validate.assert_not_awaited()
        self.assertEqual(len(self.users.docs), 1)

    async def test_signup_race_on_the_unique_email_index(self):
        self.users.insert_error = DuplicateKeyError('E11000 duplicate key')
        result = await self.signup()
        self.assertEqual(result['statusCode'], 409)
        self.assertNotIn('token', result)

    async def test_password_and_canvas_token_are_never_stored_in_plain_text(self):
        result = await self.signup()
        saved = self.users.docs[0]
        self.assertNotEqual(saved['password'], PASSWORD)
        self.assertTrue(bcrypt.checkpw(PASSWORD.encode(), saved['password'].encode()))
        self.assertNotEqual(saved['canvas_api_token'], CANVAS_TOKEN)
        self.assertEqual(decrypt_token(KEY, saved['canvas_api_token']), CANVAS_TOKEN)
        self.assertNotIn(PASSWORD, str(result))
        self.assertNotIn(CANVAS_TOKEN, str(result))

    async def test_canvas_token_expiration_is_saved_when_known(self):
        expires = naive_utc_now() + timedelta(days=90)
        self.expiration.return_value = (200, expires)
        await self.signup()
        self.assertEqual(self.users.docs[0]['canvas_token_expiration'], expires)

    async def test_expiration_is_left_out_when_canvas_wont_say(self):
        self.expiration.return_value = (401, None)
        result = await self.signup()
        self.assertIn('token', result)
        self.assertNotIn('canvas_token_expiration', self.users.docs[0])

    async def test_unexpected_error_is_a_generic_500(self):
        self.validate.side_effect = Exception('boom')
        with self.assertLogs('services.achieveup_auth_service', 'ERROR'):
            result = await self.signup()
        self.assertEqual(result, {'error': 'Internal server error', 'statusCode': 500})


class LoginTest(AuthServiceTestCase):
    def setUp(self):
        super().setUp()
        self.users.docs.append(user_doc(role='instructor', canvas_token_type='instructor',
                                        has_student_access=True))

    async def test_login_returns_token_and_user(self):
        result = await auth_service.achieveup_login('ann@ucf.edu', PASSWORD)
        payload = jwt.decode(result['token'], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        self.assertEqual((payload['user_id'], payload['role'], payload['canvas_token_type']),
                         ('u1', 'instructor', 'instructor'))
        self.assertEqual(result['user'], {
            'id': 'u1', 'name': 'Ann', 'email': 'ann@ucf.edu', 'role': 'instructor',
            'hasCanvasToken': True, 'canvasTokenType': 'instructor', 'canvas_student_id': 555,
            'has_student_access': True})

    async def test_wrong_password_and_unknown_email_look_the_same(self):
        wrong = await auth_service.achieveup_login('ann@ucf.edu', 'Wr0ngPass')
        unknown = await auth_service.achieveup_login('bob@ucf.edu', PASSWORD)
        self.assertEqual(wrong['statusCode'], 401)
        self.assertEqual(wrong, unknown)

    async def test_older_account_without_token_type_logs_in_as_student_type(self):
        self.users.docs = [user_doc()]
        del self.users.docs[0]['canvas_token_type']
        result = await auth_service.achieveup_login('ann@ucf.edu', PASSWORD)
        self.assertEqual(result['user']['canvasTokenType'], 'student')

    async def test_account_without_canvas_token(self):
        self.users.docs = [user_doc(canvas_api_token=None)]
        result = await auth_service.achieveup_login('ann@ucf.edu', PASSWORD)
        self.assertFalse(result['user']['hasCanvasToken'])


class UpdateProfileTest(AuthServiceTestCase):
    def setUp(self):
        super().setUp()
        self.users.docs.append(user_doc(canvas_token_expiration=naive_utc_now() + timedelta(days=1)))
        self.users.docs.append(user_doc(user_id='u2', email='bob@ucf.edu', name='Bob'))

    async def update(self, email='ann@ucf.edu', canvas_token=None, token_type=None):
        return await auth_service.achieveup_update_profile(token_for(), 'Ann Lee', email, canvas_token, token_type)

    async def test_name_change_keeps_canvas_settings(self):
        result = await self.update()
        self.assertEqual(result['user']['name'], 'Ann Lee')
        self.validate.assert_not_awaited()
        self.assertEqual(decrypt_token(KEY, self.users.docs[0]['canvas_api_token']), CANVAS_TOKEN)

    async def test_email_taken_by_someone_else(self):
        result = await self.update(email='bob@ucf.edu')
        self.assertEqual(result['statusCode'], 409)
        self.assertEqual(self.users.docs[0]['email'], 'ann@ucf.edu')

    async def test_email_can_change_to_an_unused_one(self):
        result = await self.update(email='ann.lee@ucf.edu')
        self.assertEqual(result['user']['email'], 'ann.lee@ucf.edu')

    async def test_short_canvas_token_is_rejected(self):
        result = await self.update(canvas_token='short')
        self.assertEqual(result['statusCode'], 400)
        self.validate.assert_not_awaited()
        self.assertEqual(self.users.docs[0]['name'], 'Ann')

    async def test_new_instructor_token_promotes_to_instructor(self):
        self.canvas_answers['instructor'] = INSTRUCTOR_OK
        new_token = 'n' * 64
        result = await self.update(canvas_token=new_token)
        self.assertEqual(result['user']['role'], 'instructor')
        self.assertEqual(result['user']['canvas_student_id'], 900)
        self.assertEqual(decrypt_token(KEY, self.users.docs[0]['canvas_api_token']), new_token)

    async def test_claimed_instructor_type_is_ignored(self):
        self.users.docs[0].update(role='instructor', canvas_token_type='instructor')
        result = await self.update(canvas_token='n' * 64, token_type='instructor')
        # Canvas says the new token is only a student's, so the role drops.
        self.assertEqual(result['user']['role'], 'student')
        self.assertEqual(self.users.docs[0]['canvas_token_type'], 'student')

    async def test_invalid_canvas_token_changes_nothing(self):
        self.canvas_answers['student'] = NOT_VALID
        result = await self.update(canvas_token='n' * 64)
        self.assertEqual(result['statusCode'], 400)
        self.assertEqual(self.users.docs[0]['name'], 'Ann')
        self.assertEqual(decrypt_token(KEY, self.users.docs[0]['canvas_api_token']), CANVAS_TOKEN)

    async def test_old_expiration_is_cleared_when_new_one_is_unknown(self):
        self.expiration.return_value = (500, None)
        await self.update(canvas_token='n' * 64)
        self.assertNotIn('canvas_token_expiration', self.users.docs[0])

    async def test_invalid_login_is_returned(self):
        result = await auth_service.achieveup_update_profile('bad', 'Ann', 'ann@ucf.edu')
        self.assertEqual(result['statusCode'], 401)


class ChangePasswordTest(AuthServiceTestCase):
    def setUp(self):
        super().setUp()
        self.users.docs.append(user_doc())

    async def test_password_is_changed(self):
        result = await auth_service.achieveup_change_password(token_for(), PASSWORD, 'NewPassw0rd')
        self.assertEqual(result, {'message': 'Password updated successfully'})
        stored = self.users.docs[0]['password'].encode()
        self.assertTrue(bcrypt.checkpw(b'NewPassw0rd', stored))
        self.assertFalse(bcrypt.checkpw(PASSWORD.encode(), stored))

    async def test_wrong_current_password(self):
        before = self.users.docs[0]['password']
        result = await auth_service.achieveup_change_password(token_for(), 'Wr0ngPass', 'NewPassw0rd')
        self.assertEqual((result['statusCode'], result['message']), (400, 'Current password is incorrect'))
        self.assertEqual(self.users.docs[0]['password'], before)

    async def test_weak_new_password(self):
        before = self.users.docs[0]['password']
        result = await auth_service.achieveup_change_password(token_for(), PASSWORD, 'weak')
        self.assertEqual(result['statusCode'], 400)
        self.assertEqual(self.users.docs[0]['password'], before)

    async def test_invalid_login_is_returned(self):
        result = await auth_service.achieveup_change_password('bad', PASSWORD, 'NewPassw0rd')
        self.assertEqual(result['statusCode'], 401)


class StoredCanvasTokenTest(AuthServiceTestCase):
    async def test_token_is_decrypted(self):
        self.users.docs.append(user_doc())
        self.assertEqual(await auth_service.get_user_canvas_token('u1'), CANVAS_TOKEN)

    async def test_no_user_or_no_token(self):
        self.users.docs.append(user_doc(user_id='u2', canvas_api_token=None))
        self.assertIsNone(await auth_service.get_user_canvas_token('nobody'))
        self.assertIsNone(await auth_service.get_user_canvas_token('u2'))

    async def test_undecryptable_token_is_none(self):
        self.users.docs.append(user_doc(canvas_api_token=b'\x00' * 40))
        with self.assertLogs('services.achieveup_auth_service', 'ERROR'):
            self.assertIsNone(await auth_service.get_user_canvas_token('u1'))


if __name__ == '__main__':
    unittest.main()
