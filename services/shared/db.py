"""
Shared database connection pool and utilities.
All services use this module for Postgres access.
"""

import os
import logging
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
from psycopg2 import pool, extras

logger = logging.getLogger(__name__)

_pool: pool.ThreadedConnectionPool | None = None


def get_pool() -> pool.ThreadedConnectionPool:
    """Lazy-initialize a connection pool. Single operator = small pool."""
    global _pool
    if _pool is None or _pool.closed:
        _pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,  # Single operator needs very few connections
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", 5432)),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            connect_timeout=10,
            options="-c statement_timeout=120000",  # 2 min query timeout
        )
    return _pool


@contextmanager
def get_conn() -> Generator:
    """Context manager that returns and properly releases a pooled connection."""
    p = get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


@contextmanager
def get_cursor(dict_cursor: bool = False) -> Generator:
    """Context manager for a cursor. Auto-commits on success, rolls back on error."""
    cursor_factory = extras.RealDictCursor if dict_cursor else None
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()


def execute_batch(query: str, data: list[tuple], page_size: int = 500) -> int:
    """Batch execute for high-throughput upserts. Returns row count."""
    with get_conn() as conn:
        cur = conn.cursor()
        extras.execute_batch(cur, query, data, page_size=page_size)
        count = cur.rowcount
        cur.close()
        return count


def audit(service: str, action: str, detail: dict[str, Any] | None = None) -> None:
    """Write to the audit trail. Fire and forget — never fails the caller."""
    try:
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO audit_trail (service, action, detail) VALUES (%s, %s, %s)",
                (service, action, extras.Json(detail)),
            )
    except Exception as e:
        logger.warning(f"Audit write failed (non-fatal): {e}")


def close_pool() -> None:
    """Cleanly close the connection pool."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None
