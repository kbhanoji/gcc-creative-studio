# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Start a stopped Cloud SQL instance on demand (developlocal cost saving).

In development the Cloud SQL instance is stopped when not in use
(activation policy NEVER). With DB_AUTOSTART=true the backend starts it again
when a request arrives:

  1. set the instance activation policy to ALWAYS through the Cloud SQL Admin API
     (the backend service account needs roles/cloudsql.editor),
  2. wait until a real database connection succeeds,
  3. run the pending migrations, then mark the database ready.

Requests wait up to DB_AUTOSTART_REQUEST_WAIT_SECONDS for this. Firebase Hosting
proxies /api/** with a 60 s limit, so after that the request gets
503 + X-DB-Starting and the frontend retries automatically.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from src.config.config_service import config_service

logger = logging.getLogger(__name__)

SQLADMIN = "https://sqladmin.googleapis.com/v1"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _instance_parts() -> tuple[str, str]:
    """'project:region:instance' -> (project, instance)."""
    project, _region, instance = config_service.INSTANCE_CONNECTION_NAME.split(":")
    return project, instance


def _ensure_activation_policy_always(timeout_s: int) -> None:
    """Blocking: start the instance if its activation policy isn't ALWAYS."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=SCOPES)
    session = AuthorizedSession(credentials)
    project, instance = _instance_parts()
    url = f"{SQLADMIN}/projects/{project}/instances/{instance}"

    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    policy = resp.json().get("settings", {}).get("activationPolicy")
    if policy == "ALWAYS":
        return

    logger.warning(
        "Cloud SQL instance %s is stopped (activationPolicy=%s); starting it.",
        instance,
        policy,
    )
    resp = session.patch(
        url, json={"settings": {"activationPolicy": "ALWAYS"}}, timeout=30
    )
    resp.raise_for_status()
    operation = resp.json().get("name")
    deadline = time.monotonic() + timeout_s
    while operation and time.monotonic() < deadline:
        op = session.get(
            f"{SQLADMIN}/projects/{project}/operations/{operation}", timeout=30
        )
        op.raise_for_status()
        body = op.json()
        if body.get("status") == "DONE":
            if body.get("error"):
                raise RuntimeError(f"Cloud SQL start failed: {body['error']}")
            logger.info("Cloud SQL instance %s started.", instance)
            return
        time.sleep(5)
    if operation:
        raise TimeoutError(f"Cloud SQL instance {instance} did not start in {timeout_s}s")


async def _wait_for_connection(timeout_s: int) -> None:
    from src.database import get_connection

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            conn = await get_connection()
            await conn.close()
            return
        except Exception as e:  # noqa: BLE001 - keep trying until the deadline
            if time.monotonic() > deadline:
                raise TimeoutError(f"database not reachable after {timeout_s}s: {e}") from e
            await asyncio.sleep(5)


class DatabaseAutostart:
    """Process-wide state: one wake-up attempt at a time, shared by all requests."""

    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(config_service.DB_AUTOSTART and config_service.INSTANCE_CONNECTION_NAME)

    def start(self, on_ready: Callable[[], Awaitable[None]]) -> None:
        """Begin waking the database in the background (no-op if ready or already waking)."""
        if self.ready.is_set() or (self._task and not self._task.done()):
            return
        self._task = asyncio.create_task(self._run(on_ready))

    async def _run(self, on_ready: Callable[[], Awaitable[None]]) -> None:
        timeout_s = config_service.DB_AUTOSTART_TIMEOUT_SECONDS
        try:
            await asyncio.to_thread(_ensure_activation_policy_always, timeout_s)
            await _wait_for_connection(timeout_s)
            await on_ready()
            self.last_error = None
            self.ready.set()
            logger.info("Database is ready.")
        except Exception as e:  # noqa: BLE001 - the next request retries
            self.last_error = str(e)
            logger.error("Database autostart failed: %s", e, exc_info=True)

    async def wait(self, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(asyncio.shield(self.ready.wait()), timeout_s)
            return True
        except asyncio.TimeoutError:
            return False


db_autostart = DatabaseAutostart()
