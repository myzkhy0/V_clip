"""
db.py — PostgreSQL connection helpers and schema initializer.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

_SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection():
    """Return a new psycopg2 connection using DATABASE_URL."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Run schema.sql to create tables (IF NOT EXISTS)."""
    with open(_SCHEMA_FILE, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                """
                ALTER TABLE videos
                ADD COLUMN IF NOT EXISTS tags_text TEXT NOT NULL DEFAULT ''
                """
            )
        conn.commit()
        print("[db] Schema initialized successfully.")
    finally:
        conn.close()


def execute(query: str, params: tuple = ()) -> None:
    """Execute a write query (INSERT / UPDATE / DELETE)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def execute_many(query: str, params_list: list[tuple]) -> None:
    """Execute a write query for many rows."""
    if not params_list:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(query, params_list)
        conn.commit()
    finally:
        conn.close()


def fetchall(query: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return all rows as dicts."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()


def fetchone(query: str, params: tuple = ()) -> dict | None:
    """Run a SELECT and return a single row as a dict, or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchone()
    finally:
        conn.close()
