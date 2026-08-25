from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from incident_intelligence.settings import validate_mysql_database_url


def get_engine(database_url: str) -> Engine:
    validate_mysql_database_url(database_url)
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        connect_args={"charset": "utf8mb4"},
    )
    event.listen(engine, "connect", _set_utc_timezone)
    return engine


def _set_utc_timezone(
    dbapi_connection: DBAPIConnection,
    connection_record: ConnectionPoolEntry,
) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
    finally:
        cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
