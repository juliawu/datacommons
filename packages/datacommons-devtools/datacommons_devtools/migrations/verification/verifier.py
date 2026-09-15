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

"""End-to-end schema migration verification workflows for Cloud Spanner.

This module provides high-level workflows for verifying database migrations:
1. Static DDL validation: Ensures schema files exist, are non-empty, and obey
   topological dependency constraints without requiring a live database.
2. Live emulator verification: Applies baseline seeding to Database A, executes
   sequential chronological migrations via MigrationRunner, applies target schema
   directly to Database B, and computes a deep structural diff to prove equivalence.
3. Idempotency verification: Proves that re-running migrations on an up-to-date
   database produces zero changes.
"""

import logging
from pathlib import Path

from datacommons_db.clients.spanner_client import ExecutionStatus, SpannerClient
from datacommons_db.migrations.migration_runner import MigrationRunner
from datacommons_devtools.migrations.verification.comparator import compare_schemas
from datacommons_devtools.migrations.verification.extractor import (
    extract_schema_metadata,
)
from datacommons_devtools.migrations.verification.models import SchemaDiffResult
from datacommons_devtools.migrations.verification.parser import load_ddl_statements
from datacommons_devtools.migrations.verification.validator import (
    assert_valid_ddl_topological_order,
    validate_ddl_topological_order,
)

logger = logging.getLogger(__name__)


def verify_static_schema(schema_source: Path | str) -> list[str]:
    """Validate that a schema file exists and its DDL statements obey topological order.

    Args:
        schema_source: Path to a .sql file or a string containing DDL statements.

    Returns:
        List of error description strings (empty if order is valid).
    """
    statements = load_ddl_statements(schema_source)
    if not statements:
        return [f"Schema source '{schema_source}' contains no valid DDL statements."]
    return validate_ddl_topological_order(statements)


def assert_valid_static_schema(schema_source: Path | str) -> list[str]:
    """Assert that a schema file exists, is non-empty, and satisfies topological constraints.

    Args:
        schema_source: Path to a .sql file or a string containing DDL statements.

    Returns:
        List of parsed non-empty DDL statement strings.

    Raises:
        ValueError: If the schema contains no DDL statements or violates topological ordering.
    """
    statements = load_ddl_statements(schema_source)
    if not statements:
        raise ValueError(
            f"Schema source '{schema_source}' contains no valid DDL statements."
        )
    assert_valid_ddl_topological_order(statements)
    return statements


def verify_migrated_schema(
    client_migrated: SpannerClient,
    client_target: SpannerClient,
    baseline_ddl_source: Path | str,
    target_ddl_source: Path | str,
    migration_runner: MigrationRunner | None = None,
) -> SchemaDiffResult:
    """Execute the end-to-end schema migration verification workflow against two databases.

    Workflow:
    1. Apply baseline schema DDL to Database A (Migrated).
    2. Execute all chronological migrations via MigrationRunner on Database A.
    3. Apply target schema DDL directly to Database B (Target).
    4. Extract INFORMATION_SCHEMA metadata from both databases.
    5. Perform deep structural comparison between the two schemas.

    Args:
        client_migrated: SpannerClient connected to Database A (migrated).
        client_target: SpannerClient connected to Database B (target).
        baseline_ddl_source: Path or string containing baseline schema DDL.
        target_ddl_source: Path or string containing target schema DDL.
        migration_runner: Optional MigrationRunner instance. If None, instantiated
            with client_migrated.

    Returns:
        SchemaDiffResult indicating whether schemas match and listing any differences.

    Raises:
        RuntimeError: If DDL execution or migration execution fails on either database.
    """
    # 1. Setup Database A: Apply baseline DDL
    logger.info("Applying baseline schema DDL to Database A (Migrated)")
    baseline_ddls = load_ddl_statements(baseline_ddl_source)
    res_baseline = client_migrated.execute_ddl(baseline_ddls)
    if res_baseline.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to apply baseline schema to Database A: {res_baseline.error_message}"
        )

    # 2. Execute all pending migrations on Database A
    logger.info("Executing migrations via MigrationRunner on Database A")
    runner = migration_runner or MigrationRunner(spanner_client=client_migrated)
    migration_results = runner.run_migrations()
    if not migration_results:
        raise RuntimeError("No migrations were discovered or executed on Database A.")
    if any(r.status != ExecutionStatus.SUCCESS for r in migration_results):
        failed = [r for r in migration_results if r.status != ExecutionStatus.SUCCESS]
        raise RuntimeError(f"{len(failed)} migration(s) failed on Database A: {failed}")

    # 3. Setup Database B: Apply target schema DDL directly
    logger.info("Applying target schema DDL to Database B (Target)")
    target_ddls = load_ddl_statements(target_ddl_source)
    res_target = client_target.execute_ddl(target_ddls)
    if res_target.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to apply target schema to Database B: {res_target.error_message}"
        )

    # 4. Extract metadata from both databases and compare
    logger.info("Extracting schema metadata from both databases")
    schema_migrated = extract_schema_metadata(client_migrated)
    schema_target = extract_schema_metadata(client_target)

    logger.info("Performing structural schema comparison")
    return compare_schemas(
        schema_migrated,
        schema_target,
        name_a="Database A (Migrated)",
        name_b="Database B (Target)",
    )


def verify_migration_idempotency(
    client_migrated: SpannerClient,
    baseline_ddl_source: Path | str,
    migration_runner: MigrationRunner | None = None,
) -> bool:
    """Verify that re-running migrations on an up-to-date database performs zero changes.

    Args:
        client_migrated: SpannerClient connected to an ephemeral database.
        baseline_ddl_source: Path or string containing baseline schema DDL.
        migration_runner: Optional MigrationRunner instance.

    Returns:
        True if migrations are idempotent.

    Raises:
        RuntimeError: If baseline seeding or initial migration fails.
        AssertionError: If second run applies any changes.
    """
    # 1. Apply baseline and run initial migrations
    logger.info("Seeding initial database for idempotency test")
    baseline_ddls = load_ddl_statements(baseline_ddl_source)
    res_baseline = client_migrated.execute_ddl(baseline_ddls)
    if res_baseline.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to apply baseline schema in idempotency test: {res_baseline.error_message}"
        )

    runner = migration_runner or MigrationRunner(spanner_client=client_migrated)
    first_run = runner.run_migrations()
    if not first_run:
        raise RuntimeError("Initial migration run executed 0 migrations.")

    # 2. Second run should be a no-op
    logger.info("Re-executing migrations and asserting no-op (idempotency)")
    second_run = runner.run_migrations()
    if second_run:
        raise AssertionError(
            f"Expected second migration run to be a no-op, but executed {len(second_run)} changes."
        )

    return True

