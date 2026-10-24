import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "learning.sqlite3")


@pytest.fixture
def config():
    return Settings(_env_file=None, app_mode="demo", dataset="sample", llm_api_key="", tavily_api_key="")


@pytest.fixture
def client(config, store):
    app = create_app(config, store=store)
    with TestClient(app, headers={"X-Learning-Client": "workbench"}) as c:
        yield c
