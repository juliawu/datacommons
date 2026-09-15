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

"""Dynamic Cloud Spanner emulator migration integration tests.

Verifies that applying baseline seeding followed by sequential chronological migrations
produces an identical schema state to applying target schema_latest.sql directly, and
that migrations are idempotent.
"""

from datacommons_db.clients.spanner_client import ExecutionStatus
from datacommons_db.migrations.migration_runner import MigrationRunner
from datacommons_devtools.migrations.verification import (
    compare_schemas,
    extract_schema_metadata,
    load_ddl_statements,
)

from .conftest import SCHEMA_BASELINE_SQL_PATH, SCHEMA_LATEST_SQL_PATH, step_progress


def test_migrated_schema_matches_target_schema(ephemeral_database_pair):
    """Verify that baseline + sequential migrations produces an identical schema to schema_latest.sql."""
    client_migrated, client_target = ephemeral_database_pair

    # 1. Setup Database A (Migrated): Apply baseline DDL
    with step_progress("[1/4] Applying baseline schema DDL to Database A (Migrated)"):
        baseline_ddls = load_ddl_statements(SCHEMA_BASELINE_SQL_PATH)
        res_baseline = client_migrated.execute_ddl(baseline_ddls)
        assert res_baseline.status == ExecutionStatus.SUCCESS, (
            f"Failed to apply baseline schema to Database A: {res_baseline.error_message}"
        )

    # 2. Execute all pending migrations on Database A
    with step_progress("[2/4] Executing migrations via MigrationRunner on Database A"):
        runner = MigrationRunner(spanner_client=client_migrated)
        migration_results = runner.run_migrations()
        assert len(migration_results) > 0, (
            "No migrations were discovered or executed on Database A"
        )
        assert all(r.status == ExecutionStatus.SUCCESS for r in migration_results), (
            "One or more migrations failed during execution on Database A"
        )

    # 3. Setup Database B (Target): Apply schema_latest.sql directly
    with step_progress(
        "[3/4] Applying target schema_latest.sql DDL to Database B (Target)"
    ):
        target_ddls = load_ddl_statements(SCHEMA_LATEST_SQL_PATH)
        res_target = client_target.execute_ddl(target_ddls)
        assert res_target.status == ExecutionStatus.SUCCESS, (
            f"Failed to apply target schema_latest.sql to Database B: {res_target.error_message}"
        )

    # 4. Extract INFORMATION_SCHEMA metadata from both databases and compare
    with step_progress(
        "[4/4] Extracting INFORMATION_SCHEMA & validating schema equality"
    ):
        schema_a = extract_schema_metadata(client_migrated)
        schema_b = extract_schema_metadata(client_target)

        diff_result = compare_schemas(
            schema_a,
            schema_b,
            name_a="Database A (Migrated)",
            name_b="Database B (Target)",
        )

        assert diff_result.is_match is True, (
            "Schema mismatch detected between Migrated Database and Target schema_latest.sql:\n"
            + "\n".join(f"  - {d}" for d in diff_result.differences)
        )


def test_schema_migration_idempotency(ephemeral_database_pair):
    """Verify that re-running migrations on an up-to-date database is a no-op."""
    client_migrated, _ = ephemeral_database_pair

    # 1. Apply baseline and run initial migrations
    with step_progress(
        "[1/2] Seeding initial database and applying initial migrations"
    ):
        baseline_ddls = load_ddl_statements(SCHEMA_BASELINE_SQL_PATH)
        res_baseline = client_migrated.execute_ddl(baseline_ddls)
        assert res_baseline.status == ExecutionStatus.SUCCESS, (
            f"Failed to apply baseline schema in idempotency test: {res_baseline.error_message}"
        )

        runner = MigrationRunner(spanner_client=client_migrated)
        first_run = runner.run_migrations()
        assert len(first_run) >= 1

    # 2. Second run should be a no-op
    with step_progress(
        "[2/2] Re-executing migrations and asserting 0 changes applied (idempotency)"
    ):
        second_run = runner.run_migrations()
        assert second_run == [], "Expected second migration run to be empty (no-op)"
