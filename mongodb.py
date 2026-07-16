from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
)
import logging
import sys

from config import Config

# Private _client and _db vars.
_client: AsyncIOMotorClient
_db: AsyncIOMotorDatabase

logger: logging.Logger = logging.getLogger(__name__)

def initialize_db() -> None | Exception:
    """This function will set the _client and _db variables used as global client and db connections."""
    try:
        global _client, _db
        _client = AsyncIOMotorClient(
            Config.DB_CONNECTION_STRING,
            tls=(Config.ENV != 'development'),
            tlsAllowInvalidCertificates=(Config.ENV == 'development'),
            connectTimeoutMS=30000,
            serverSelectionTimeoutMS=30000
        )

        _db = _client[Config.DATABASE]

        logger.log("MongoDB client initialized successfully.")
    except Exception as err:
        logger.error(f"Failed to initialize MongoDB client: {err}")
        sys.exit(69)

def get_client() -> AsyncIOMotorClient:
    """This function returns the MongoDB client."""
    return _client

def get_db() -> AsyncIOMotorDatabase:
    """This fucntion returns the MongoDB database connection."""
    return _db

def close_connection() -> None | Exception:
    """This function closes the MongoDB client. If this fails, it will log the error."""
    try:
        _client.close()
    except Exception as err:
        return err

initialize_db()