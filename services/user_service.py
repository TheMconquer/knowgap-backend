from config import Config
from utils.encryption_utils import encrypt_token

from mongodb import get_db

# Async MongoDB connection
db = get_db()

tokens_collection = db[Config.TOKENS_COLLECTION]

async def add_user(user_id, access_token, course_ids, link):
    """Add or update a user token in the database."""
    encrypted_token = encrypt_token(bytes.fromhex(Config.HEX_ENCRYPTION_KEY), access_token)

    await tokens_collection.update_one(
        {'_id': user_id},
        {"$set": {"auth": encrypted_token, "course_ids": course_ids, "link": link}}, 
        upsert=True
    )

    # return updated user details
    updated_user = await tokens_collection.find_one({'_id': user_id}, {'_id': 0})
    return updated_user
