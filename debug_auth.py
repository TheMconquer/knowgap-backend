#!/usr/bin/env python3
"""
Debug script to check user authentication issues
"""
import asyncio
import sys
import os
from dotenv import load_dotenv

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from mongodb import get_db
from config import Config
import bcrypt

async def check_users():
    """Check all users in AchieveUp_Users collection"""
    db = get_db()
    users_collection = db[Config.ACHIEVEUP_USERS_COLLECTION]
    
    print("\n" + "="*60)
    print("AchieveUp Users in Database:")
    print("="*60)
    
    users = await users_collection.find({}).to_list(length=100)
    
    if not users:
        print("\n⚠️  NO USERS FOUND in database!")
        print("You need to SIGN UP first before you can LOGIN.")
        return
    
    print(f"\nFound {len(users)} user(s):\n")
    for user in users:
        print(f"  Email: {user.get('email')}")
        print(f"  Name: {user.get('name')}")
        print(f"  Role: {user.get('role')}")
        print(f"  User ID: {user.get('user_id')}")
        print(f"  Has Canvas Token: {'canvas_api_token' in user}")
        print("-" * 60)

async def test_password(email: str, password: str):
    """Test if a password is correct for a given email"""
    db = get_db()
    users_collection = db[Config.ACHIEVEUP_USERS_COLLECTION]
    
    print(f"\n" + "="*60)
    print(f"Testing login for: {email}")
    print("="*60)
    
    user = await users_collection.find_one({'email': email})
    
    if not user:
        print(f"\n❌ User with email '{email}' NOT FOUND in database!")
        return False
    
    print(f"\n✓ User found!")
    print(f"  Name: {user.get('name')}")
    print(f"  Role: {user.get('role')}")
    
    # Check password
    hashed_password = user.get('password', '')
    if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
        print(f"\n✓ PASSWORD MATCH! Login should work.")
        return True
    else:
        print(f"\n❌ PASSWORD MISMATCH! Password is incorrect.")
        return False

async def main():
    """Main debug function"""
    print("\n🔍 AchieveUp Authentication Debug Tool\n")
    
    # Check DB connection string
    if not Config.DB_CONNECTION_STRING:
        print("❌ ERROR: DB_CONNECTION_STRING not set in .env!")
        return
    
    print(f"Database: {Config.DATABASE}")
    print(f"Collection: {Config.ACHIEVEUP_USERS_COLLECTION}\n")
    
    # Check all users
    await check_users()
    
    # Test specific login if provided
    if len(sys.argv) > 2:
        email = sys.argv[1]
        password = sys.argv[2]
        await test_password(email, password)
    else:
        print("\n" + "="*60)
        print("To test a specific login, run:")
        print("  python debug_auth.py <email> <password>")
        print("="*60)

if __name__ == '__main__':
    asyncio.run(main())
