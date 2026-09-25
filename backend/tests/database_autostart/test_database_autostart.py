"""Tests for the developlocal Cloud SQL autostart."""

import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/../../"))
os.environ.setdefault("PROJECT_ID", "test-project")

from src import database_autostart as da  # noqa: E402


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _session(policy, op_statuses=("DONE",)):
    s = MagicMock()
    statuses = iter(op_statuses)
    s.get.side_effect = lambda url, timeout: FakeResponse(
        {"status": next(statuses)} if "/operations/" in url else {"settings": {"activationPolicy": policy}}
    )
    s.patch.return_value = FakeResponse({"name": "op-123"})
    return s


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(da.config_service, "INSTANCE_CONNECTION_NAME", "proj:us-central1:creative-studio-db-1")
    monkeypatch.setattr(da.config_service, "DB_AUTOSTART", True)


def _run_ensure(session):
    with patch("google.auth.default", return_value=(MagicMock(), "proj")), \
         patch("google.auth.transport.requests.AuthorizedSession", return_value=session), \
         patch.object(da.time, "sleep"):
        da._ensure_activation_policy_always(60)


def test_running_instance_is_left_alone():
    s = _session("ALWAYS")
    _run_ensure(s)
    s.patch.assert_not_called()


def test_stopped_instance_is_started_and_operation_awaited():
    s = _session("NEVER", op_statuses=("RUNNING", "DONE"))
    _run_ensure(s)
    s.patch.assert_called_once()
    assert s.patch.call_args.kwargs["json"] == {"settings": {"activationPolicy": "ALWAYS"}}
    assert s.patch.call_args.args[0].endswith("/projects/proj/instances/creative-studio-db-1")


def test_enabled_only_with_flag_and_instance(monkeypatch):
    assert da.DatabaseAutostart().enabled
    monkeypatch.setattr(da.config_service, "DB_AUTOSTART", False)
    assert not da.DatabaseAutostart().enabled


def test_wake_runs_migrations_once_then_ready():
    async def scenario():
        auto = da.DatabaseAutostart()
        calls = []

        async def migrate():
            calls.append(1)

        async def connected(_timeout):
            return None

        with patch.object(da, "_ensure_activation_policy_always"), \
             patch.object(da, "_wait_for_connection", connected):
            auto.start(migrate)
            auto.start(migrate)  # second request while waking: no second task
            assert await auto.wait(5)
        assert calls == [1] and auto.ready.is_set() and auto.last_error is None

    asyncio.run(scenario())


def test_failed_wake_reports_error_and_can_retry():
    async def scenario():
        auto = da.DatabaseAutostart()

        async def migrate():
            pass

        def boom(_timeout):
            raise RuntimeError("permission denied")

        with patch.object(da, "_ensure_activation_policy_always", boom):
            auto.start(migrate)
            assert not await auto.wait(0.5)
        assert "permission denied" in auto.last_error and not auto.ready.is_set()

        async def connected(_timeout):
            return None

        with patch.object(da, "_ensure_activation_policy_always"), \
             patch.object(da, "_wait_for_connection", connected):
            auto.start(migrate)  # next request retries
            assert await auto.wait(5)

    asyncio.run(scenario())
