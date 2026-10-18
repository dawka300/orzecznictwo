"""Shared fixtures: a scratch MariaDB database, skipped without it.

The scratch database comes from TEST_DB_DSN (default: the docker-compose
MariaDB's szkielet_test, created by docker/mariadb-init.sql). Never point it
at a real database: the tests drop all its tables.
"""

import os

import pymysql
import pytest

from judgments_core import db

TEST_DSN = os.environ.get("TEST_DB_DSN", "mysql://szkielet:szkielet@127.0.0.1:3306/szkielet_test")


@pytest.fixture
def conn():
    try:
        c = db.connect(TEST_DSN, connect_timeout=2)
    except pymysql.err.OperationalError as e:
        pytest.skip(f"brak testowej MariaDB ({e})")
    with c.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("SHOW TABLES")
        for (table,) in cur.fetchall():
            cur.execute(f"DROP TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    c.commit()
    yield c
    c.close()
