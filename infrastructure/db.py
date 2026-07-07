from collections.abc import Generator
from contextlib import contextmanager

import oracledb

from config import settings as _settings


class DatabaseConnection:
    def __init__(self, pool: oracledb.ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_settings(cls, settings=_settings) -> "DatabaseConnection":
        dsn = (
            f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)"
            f"(HOST={settings.oracle_host})(PORT={settings.oracle_port}))"
            f"(CONNECT_DATA=(SERVICE_NAME={settings.oracle_service})))"
        )
        pool = oracledb.create_pool(
            user=settings.oracle_user,
            password=settings.oracle_password,
            dsn=dsn,
            min=1,
            max=5,
            increment=1,
        )
        return cls(pool)

    @contextmanager
    def cursor(self) -> Generator[oracledb.Cursor, None, None]:
        connection = self._pool.acquire()
        try:
            cursor = connection.cursor()
            try:
                yield cursor
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            self._pool.release(connection)

    def close(self) -> None:
        self._pool.close()
