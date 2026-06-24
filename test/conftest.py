import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app


class MockResponse:
    def __init__(self, data):
        self.data = data


class MockQuery:
    def __init__(self, table_data):
        self._table_data = table_data

    def select(self, *args):
        return self

    def eq(self, field, value):
        self._table_data = [r for r in self._table_data if r.get(field) == value]
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        return MockResponse(self._table_data)

    def insert(self, data):
        self._data_to_insert = data
        return self


class MockSupabase:
    def __init__(self):
        self._tables = {}

    def from_(self, table):
        return MockQuery(self._tables.get(table, []))


@pytest.fixture
def mock_supabase():
    sb = MockSupabase()
    with patch('router.get_admin_db', return_value=sb):
        yield sb


@pytest.fixture
def mock_whatsapp():
    with patch('router.send_text', new_callable=MagicMock) as mock:
        yield mock


@pytest.fixture
def client():
    app.dependency_overrides = {}
    return TestClient(app)
