from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
    AsyncIOMotorCollection,
)


from config import Config

_client: AsyncIOMotorClient
_db: AsyncIOMotorDatabase

def initialize_db() -> None | Exception:
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

    except Exception as err:
        return err

def get_client() -> AsyncIOMotorClient:
    return _client

def get_db() -> AsyncIOMotorDatabase:
    return _db

def close_connection() -> None | Exception:
    try:
        _client.close()
    except Exception as err:
        return err

initialize_db()