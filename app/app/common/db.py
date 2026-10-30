import mysql.connector
from mysql.connector import Error
from flask import current_app

from app.common.logger import (
    get_logger,
    is_sql_logging_enabled,
    normalize_sql,
    sanitize_payload,
)


class LoggedCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._logger = get_logger("sql")

    def execute(self, operation, params=None, *args, **kwargs):
        self._log_query(operation, params)
        try:
            if params is None:
                return self._cursor.execute(operation, *args, **kwargs)
            return self._cursor.execute(operation, params, *args, **kwargs)
        except Exception:
            get_logger("exception").exception(
                "SQL_EXECUTE_FAILED sql=%s params=%s",
                normalize_sql(operation),
                sanitize_payload(params),
            )
            raise

    def executemany(self, operation, seq_params):
        batch_size = len(seq_params) if hasattr(seq_params, "__len__") else "unknown"
        self._logger.info(
            "SQL_EXECUTEMANY batch_size=%s sql=%s",
            batch_size,
            normalize_sql(operation),
        )
        try:
            return self._cursor.executemany(operation, seq_params)
        except Exception:
            get_logger("exception").exception(
                "SQL_EXECUTEMANY_FAILED batch_size=%s sql=%s",
                batch_size,
                normalize_sql(operation),
            )
            raise

    def _log_query(self, operation, params):
        self._logger.info(
            "SQL_QUERY sql=%s params=%s",
            normalize_sql(operation),
            sanitize_payload(params),
        )

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._cursor.__exit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class LoggedConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self, *args, **kwargs):
        return LoggedCursor(self._connection.cursor(*args, **kwargs))

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._connection.__exit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _db_config():
    return {
        "host": current_app.config.get("DB_HOST", "127.0.0.1"),
        "user": current_app.config.get("DB_USER", "root"),
        "password": current_app.config.get("DB_PASSWORD", ""),
        "database": current_app.config.get("DB_NAME", "your_database_name"),
        "port": int(current_app.config.get("DB_PORT", 3306)),
        "connection_timeout": int(current_app.config.get("DB_CONNECT_TIMEOUT", 10)),
        "charset": "utf8mb4",
        "use_unicode": True,
        "time_zone": "+05:30",
    }


def get_db_connection():
    try:
        connection = mysql.connector.connect(**_db_config())
        if is_sql_logging_enabled():
            return LoggedConnection(connection)
        return connection
    except Error as e:
        current_app.logger.exception("Database connection error: %s", e)
        get_logger("exception").exception("Database connection error: %s", e)
        return None


def get_db():
    connection = mysql.connector.connect(**_db_config())
    if is_sql_logging_enabled():
        return LoggedConnection(connection)
    return connection
