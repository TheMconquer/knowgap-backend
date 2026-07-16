from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
    AsyncIOMotorCollection,
)

from config import Config

_client: AsyncIOMotorClient
_db: AsyncIOMotorDatabase

def initialize_db() -> bool:
    pass

def get_client() -> AsyncIOMotorClient:
    pass

def get_db() -> AsyncIOMotorDatabase:
    pass

def close_connection() -> bool:
    pass