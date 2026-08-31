from __future__ import annotations

from types import SimpleNamespace

from codeatlas import database


class _Result:
    def all(self):
        return []


class _Session:
    def __init__(self, _engine):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def exec(self, _statement):
        return _Result()

    def commit(self):
        pass


def test_production_initialization_never_creates_schema(monkeypatch) -> None:
    create_all_calls: list[object] = []
    monkeypatch.setattr(database, "Session", _Session)
    monkeypatch.setattr(
        database.SQLModel.metadata,
        "create_all",
        lambda engine: create_all_calls.append(engine),
    )

    engine = object()
    database.initialize_database(SimpleNamespace(environment="production"), engine)

    assert create_all_calls == []


def test_test_initialization_creates_disposable_schema(monkeypatch) -> None:
    create_all_calls: list[object] = []
    monkeypatch.setattr(database, "Session", _Session)
    monkeypatch.setattr(
        database.SQLModel.metadata,
        "create_all",
        lambda engine: create_all_calls.append(engine),
    )

    engine = object()
    database.initialize_database(SimpleNamespace(environment="test"), engine)

    assert create_all_calls == [engine]
