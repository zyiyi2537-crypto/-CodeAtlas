from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.exc import OperationalError

from codeatlas import app


class _PollingStop:
    def __init__(self, cycles: int):
        self.cycles = cycles
        self.waits: list[int] = []

    def is_set(self) -> bool:
        return len(self.waits) >= self.cycles

    def wait(self, timeout: int) -> None:
        self.waits.append(timeout)


def _source(source_id: str, *, checked: datetime | None = None):
    return SimpleNamespace(id=source_id, last_checked_at=checked, poll_interval_seconds=300)


@pytest.mark.parametrize("failure_at", ["connect", "query"])
def test_source_polling_recovers_after_a_database_outage(monkeypatch, caplog, failure_at):
    session = MagicMock()
    session.__enter__.return_value = session
    result = SimpleNamespace(all=lambda: [_source("due-source")])
    outage = OperationalError("SELECT source", {}, Exception("private database detail"))
    if failure_at == "connect":
        session.__enter__.side_effect = [outage, session]
        session.exec.return_value = result
    else:
        session.exec.side_effect = [outage, result]
    monkeypatch.setattr(app, "Session", Mock(return_value=session))
    gitlab = Mock(return_value=0)
    github = Mock(return_value=0)
    submit = Mock()
    stop = _PollingStop(cycles=2)

    app.run_source_sync(object(), stop, (("gitlab", gitlab), ("github", github)), submit)

    submit.assert_called_once_with("due-source")
    assert gitlab.call_count == github.call_count == 2
    assert stop.waits == [60, 60]
    assert "retrying after 60 seconds" in caplog.text
    assert "private database detail" not in caplog.text


def test_source_polling_isolates_provider_and_external_submission_failures(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.exec.return_value.all.return_value = [
        _source("already-running"),
        _source("failed-source"),
        _source("healthy-source"),
        _source("recent-source", checked=datetime.now(UTC)),
    ]
    monkeypatch.setattr(app, "Session", Mock(return_value=session))
    gitlab = Mock(side_effect=ValueError("provider unavailable"))
    github = Mock(return_value=0)
    submit = Mock(side_effect=[RuntimeError("already running"), ValueError("outage"), None])
    stop = _PollingStop(cycles=1)

    app.run_source_sync(object(), stop, (("gitlab", gitlab), ("github", github)), submit)

    github.assert_called_once_with()
    assert [call.args[0] for call in submit.call_args_list] == [
        "already-running", "failed-source", "healthy-source",
    ]
    assert stop.waits == [60]


def test_source_polling_stops_during_repeated_failures_without_busy_retry(monkeypatch):
    session_factory = Mock(side_effect=OperationalError("SELECT", {}, Exception("outage")))
    monkeypatch.setattr(app, "Session", session_factory)
    submit = Mock()
    stop = _PollingStop(cycles=3)

    app.run_source_sync(object(), stop, (), submit)

    assert session_factory.call_count == 3
    assert stop.waits == [60, 60, 60]
    submit.assert_not_called()
