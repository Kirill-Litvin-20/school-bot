import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared import database


@pytest.fixture()
def db():
    """Return the database module connected to the PostgreSQL instance at DATABASE_URL."""
    database.init_db()
    return database
