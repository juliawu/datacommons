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

"""End-to-end automated schema migration testing framework.

Verifies that:
1. Applying baseline seeding followed by sequential chronological migrations
   produces an identical schema state to applying the golden schema.sql directly.
2. Topological dependency ordering constraints are satisfied.
3. Migrations are idempotent.
"""

import collections.abc
import contextlib
import os
import socket
import sys
import time
import uuid
from pathlib import Path

import pytest
from datacommons_db.clients.spanner_client import ExecutionStatus, SpannerClient
from datacommons_db.migrations.dependency_validator import (
    assert_valid_ddl_topological_order,
)
from datacommons_db.migrations.migration_runner import MigrationRunner
from datacommons_db.migrations.schema_comparator import (
    compare_schemas,
    extract_schema_metadata,
    load_ddl_statements,
)
from google.auth.credentials import AnonymousCredentials
from google.cloud import spanner

SCHEMA_SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "datacommons-db"
    / "datacommons_db"
    / "migrations"
    / "schema.sql"
)

BASELINE_SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "datacommons-db"
    / "datacommons_db"
    / "migrations"
    / "baseline_schema.sql"
)

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
    start_time = time.time()
    stream.write(f"\n  ⏳ {step_title}...")
    stream.flush()
    try:
        yield
        elapsed = time.time() - start_time
        stream.write(f"\r  ✅ {step_title} ({elapsed:.2f}s)\n")
        stream.flush()
    except Exception:
        elapsed = time.time() - start_time
        stream.write(f"\r  ❌ {step_title} (FAILED after {elapsed:.2f}s)\n")
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

    Tears down and drops both databases upon test completion.
    """
    uid = uuid.uuid4().hex[:8]
    db_migrated_id = f"db_migrated_{uid}"
    db_golden_id = f"db_golden_{uid}"

    with step_progress(
        f"[Setup] Creating ephemeral databases ({db_migrated_id}, {db_golden_id})"
    ):
        db_migrated = spanner_instance.database(db_migrated_id)
        op_a = db_migrated.create()
        op_a.result(timeout=60)

        db_golden = spanner_instance.database(db_golden_id)
        op_b = db_golden.create()
        op_b.result(timeout=60)

    client_a = SpannerClient(
        project_id=PROJECT_ID,
        instance_id=INSTANCE_ID,
        database_id=db_migrated_id,
        credentials=AnonymousCredentials(),
    )
    client_b = SpannerClient(
        project_id=PROJECT_ID,
        instance_id=INSTANCE_ID,
        database_id=db_golden_id,
        credentials=AnonymousCredentials(),
    )

    try:
        yield client_a, client_b
    finally:
        with step_progress(
            f"[Teardown] Dropping ephemeral databases ({db_migrated_id}, {db_golden_id})"
        ):
            with contextlib.suppress(Exception):
                db_migrated.drop()
            with contextlib.suppress(Exception):
                db_golden.drop()


# ==============================================================================
# Static DDL Validation Tests (No Emulator Required)
# ==============================================================================


def test_golden_schema_exists_and_is_non_empty():
    """Verify that schema.sql exists and contains valid DDL statements."""
    assert SCHEMA_SQL_PATH.is_file(), f"Missing golden schema file at {SCHEMA_SQL_PATH}"
    statements = load_ddl_statements(SCHEMA_SQL_PATH)
    assert len(statements) >= 4, (
        f"schema.sql should contain at least 4 DDL statements, found {len(statements)}"
    )


def test_baseline_schema_exists_and_is_non_empty():
    """Verify that baseline_schema.sql exists and contains valid DDL statements."""
    assert BASELINE_SQL_PATH.is_file(), (
        f"Missing baseline schema file at {BASELINE_SQL_PATH}"
    )
    statements = load_ddl_statements(BASELINE_SQL_PATH)
    assert len(statements) >= 3, (
        f"baseline_schema.sql should contain at least 3 DDL statements, found {len(statements)}"
    )


def test_golden_schema_ddl_topological_ordering():
    """Verify that schema.sql satisfies topological dependency constraints."""
    statements = load_ddl_statements(SCHEMA_SQL_PATH)
    assert_valid_ddl_topological_order(statements)


def test_baseline_schema_ddl_topological_ordering():
    """Verify that baseline_schema.sql satisfies topological dependency constraints."""
    statements = load_ddl_statements(BASELINE_SQL_PATH)
    assert_valid_ddl_topological_order(statements)


# ==============================================================================
# Dynamic Spanner Emulator Integration Tests
# ==============================================================================


def test_migrated_schema_matches_golden_schema(ephemeral_database_pair):
    """Verify that baseline + sequential migrations produces an identical schema to schema.sql."""
    client_migrated, client_golden = ephemeral_database_pair

    # 1. Setup Database A (Migrated): Apply baseline DDL
    with step_progress("[1/4] Applying baseline schema DDL to Database A (Migrated)"):
        baseline_ddls = load_ddl_statements(BASELINE_SQL_PATH)
        res_baseline = client_migrated.execute_ddl(baseline_ddls)
        assert res_baseline.status == ExecutionStatus.SUCCESS, (
            f"Failed to apply baseline schema to Database A: {res_baseline.error_message}"
        )

    # 2. Execute all pending migrations on Database A
    with step_progress("[2/4] Executing migrations via MigrationRunner on Database A"):
        runner = MigrationRunner(spanner_client=client_migrated)
        migration_results = runner.run_migrations()
        assert all(r.status == ExecutionStatus.SUCCESS for r in migration_results), (
            "One or more migrations failed during execution on Database A"
        )

    # 3. Setup Database B (Golden): Apply schema.sql directly
    with step_progress("[3/4] Applying golden schema.sql DDL to Database B (Golden)"):
        golden_ddls = load_ddl_statements(SCHEMA_SQL_PATH)
        res_golden = client_golden.execute_ddl(golden_ddls)
        assert res_golden.status == ExecutionStatus.SUCCESS, (
            f"Failed to apply golden schema.sql to Database B: {res_golden.error_message}"
        )

    # 4. Extract INFORMATION_SCHEMA metadata from both databases and compare
    with step_progress(
        "[4/4] Extracting INFORMATION_SCHEMA & validating schema equality"
    ):
        schema_a = extract_schema_metadata(client_migrated)
        schema_b = extract_schema_metadata(client_golden)

        diff_result = compare_schemas(
            schema_a,
            schema_b,
            name_a="Database A (Migrated)",
            name_b="Database B (Golden)",
        )

        assert diff_result.is_match is True, (
            "Schema mismatch detected between Migrated Database and Golden schema.sql:\n"
            + "\n".join(f"  - {d}" for d in diff_result.differences)
        )


def test_schema_migration_idempotency(ephemeral_database_pair):
    """Verify that re-running migrations on an up-to-date database is a no-op."""
    client_migrated, _ = ephemeral_database_pair

    # 1. Apply baseline and run initial migrations
    with step_progress(
        "[1/2] Seeding initial database and applying initial migrations"
    ):
        baseline_ddls = load_ddl_statements(BASELINE_SQL_PATH)
        client_migrated.execute_ddl(baseline_ddls)

        runner = MigrationRunner(spanner_client=client_migrated)
        first_run = runner.run_migrations()
        assert len(first_run) >= 1

    # 2. Second run should be a no-op
    with step_progress(
        "[2/2] Re-executing migrations and asserting 0 changes applied (idempotency)"
    ):
        second_run = runner.run_migrations()
        assert second_run == [], "Expected second migration run to be empty (no-op)"
