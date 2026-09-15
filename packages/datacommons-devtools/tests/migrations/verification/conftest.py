# Copyright 2026 Google LLC.
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

"""Shared fixtures and utilities for schema verification test suites."""

import collections.abc
import contextlib
import os
import socket
import sys
import time
import uuid
from pathlib import Path

import datacommons_db.migrations
import pytest
from datacommons_db.clients.spanner_client import SpannerClient
from google.auth.credentials import AnonymousCredentials
from google.cloud import spanner

SCHEMA_DIR = Path(datacommons_db.migrations.__file__).parent / "schemas"
SCHEMA_LATEST_SQL_PATH = SCHEMA_DIR / "schema_latest.sql"
SCHEMA_BASELINE_SQL_PATH = SCHEMA_DIR / "schema_baseline.sql"

PROJECT_ID = os.getenv("SPANNER_PROJECT_ID", "test-project")
INSTANCE_ID = os.getenv("SPANNER_INSTANCE_ID", "test-instance")


def is_emulator_reachable(host: str, timeout: float = 1.0) -> bool:
    """Check if the Cloud Spanner Emulator port is open and reachable.

    Args:
        host: Emulator host string (e.g. 'localhost:9010' or '127.0.0.1:9010').
        timeout: Socket connection timeout in seconds.

    Returns:
        True if connection succeeded within timeout, False otherwise.
    """
    try:
        clean_host = host.removeprefix("http://").removeprefix("https://")
        if ":" in clean_host:
            hostname, port_str = clean_host.split(":", 1)
            port = int(port_str)
        else:
            hostname = clean_host
            port = 9010
        with socket.create_connection((hostname, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


@contextlib.contextmanager
def step_progress(step_title: str) -> collections.abc.Iterator[None]:
    """Context manager that displays real-time progress indicators and timing to the terminal.

    Args:
        step_title: Human-readable description of the step being executed.
    """
    stream = (
        sys.__stderr__ if hasattr(sys, "__stderr__") and sys.__stderr__ else sys.stderr
    )
    is_tty = hasattr(stream, "isatty") and stream.isatty()
    start_time = time.time()
    if is_tty:
        stream.write(f"\n  ⏳ {step_title}...")
        stream.flush()
    try:
        yield
        elapsed = time.time() - start_time
        if is_tty:
            stream.write(f"\r  ✅ {step_title} ({elapsed:.2f}s)\n")
        else:
            stream.write(f"  ✅ {step_title} ({elapsed:.2f}s)\n")
        stream.flush()
    except Exception:
        elapsed = time.time() - start_time
        if is_tty:
            stream.write(f"\r  ❌ {step_title} (FAILED after {elapsed:.2f}s)\n")
        else:
            stream.write(f"  ❌ {step_title} (FAILED after {elapsed:.2f}s)\n")
        stream.flush()
        raise


@pytest.fixture(scope="module")
def spanner_instance():
    """Ensure Spanner instance exists on the emulator with immediate connectivity checks."""
    emulator_host = os.getenv("SPANNER_EMULATOR_HOST")
    if not emulator_host:
        pytest.skip(
            "SPANNER_EMULATOR_HOST is unset. Set SPANNER_EMULATOR_HOST='localhost:9010' and start the emulator to run live integration tests."
        )

    if not is_emulator_reachable(emulator_host, timeout=1.0):
        pytest.skip(
            f"Cloud Spanner Emulator is not reachable at '{emulator_host}'. Start the emulator (e.g. 'gcloud emulators spanner start' or docker) to run live tests."
        )

    with step_progress(f"Connecting to Spanner Emulator at {emulator_host}"):
        client = spanner.Client(project=PROJECT_ID, credentials=AnonymousCredentials())
        instance = client.instance(INSTANCE_ID)

        if not instance.exists():
            config_name = f"projects/{PROJECT_ID}/instanceConfigs/emulator-config"
            instance = client.instance(
                INSTANCE_ID,
                configuration_name=config_name,
                node_count=1,
                display_name="Test Instance",
            )
            op = instance.create()
            op.result(timeout=30)

    return instance


@pytest.fixture
def ephemeral_database_pair(spanner_instance):
    """Creates a pair of ephemeral databases on the emulator with step tracking.

    Tears down and drops both databases upon test completion. Guarantees cleanup
    even if setup fails partway through.
    """
    uid = uuid.uuid4().hex[:8]
    db_migrated_id = f"db_migrated_{uid}"
    db_target_id = f"db_target_{uid}"
    created_dbs = []

    try:
        with step_progress(
            f"[Setup] Creating ephemeral databases ({db_migrated_id}, {db_target_id})"
        ):
            db_migrated = spanner_instance.database(db_migrated_id)
            op_a = db_migrated.create()
            op_a.result(timeout=60)
            created_dbs.append(db_migrated)

            db_target = spanner_instance.database(db_target_id)
            op_b = db_target.create()
            op_b.result(timeout=60)
            created_dbs.append(db_target)

        client_migrated = SpannerClient(
            project_id=PROJECT_ID,
            instance_id=INSTANCE_ID,
            database_id=db_migrated_id,
            credentials=AnonymousCredentials(),
        )
        client_target = SpannerClient(
            project_id=PROJECT_ID,
            instance_id=INSTANCE_ID,
            database_id=db_target_id,
            credentials=AnonymousCredentials(),
        )

        yield client_migrated, client_target
    finally:
        if created_dbs:
            names = ", ".join(db.database_id for db in created_dbs)
            with step_progress(f"[Teardown] Dropping ephemeral databases ({names})"):
                for db in created_dbs:
                    with contextlib.suppress(Exception):
                        db.drop()
