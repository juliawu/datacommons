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

"""Tests for high-level schema verifier workflows.

Covers both static schema file validation and live emulator migration verification.
"""

from pathlib import Path

import pytest
from datacommons_devtools.migrations.verification.verifier import (
    assert_valid_static_schema,
    verify_migrated_schema,
    verify_migration_idempotency,
    verify_static_schema,
)

from .conftest import SCHEMA_BASELINE_SQL_PATH, SCHEMA_LATEST_SQL_PATH


def test_verify_static_schema_latest():
    """Verify that schema_latest.sql exists, is non-empty, and satisfies topological constraints."""
    assert SCHEMA_LATEST_SQL_PATH.is_file()
    statements = assert_valid_static_schema(SCHEMA_LATEST_SQL_PATH)
    assert len(statements) >= 4, (
        f"schema_latest.sql should contain at least 4 DDL statements, found {len(statements)}"
    )
    errors = verify_static_schema(SCHEMA_LATEST_SQL_PATH)
    assert errors == []


def test_verify_static_schema_baseline():
    """Verify that schema_baseline.sql exists, is non-empty, and satisfies topological constraints."""
    assert SCHEMA_BASELINE_SQL_PATH.is_file()
    statements = assert_valid_static_schema(SCHEMA_BASELINE_SQL_PATH)
    assert len(statements) >= 3, (
        f"schema_baseline.sql should contain at least 3 DDL statements, found {len(statements)}"
    )
    errors = verify_static_schema(SCHEMA_BASELINE_SQL_PATH)
    assert errors == []


def test_verify_static_schema_empty_source(tmp_path: Path):
    """Verify that empty schema sources are reported as errors."""
    empty_file = tmp_path / "empty.sql"
    empty_file.write_text("-- only comments\n", encoding="utf-8")

    errors = verify_static_schema(empty_file)
    assert len(errors) == 1
    assert "contains no valid DDL statements" in errors[0]

    with pytest.raises(ValueError, match="contains no valid DDL statements"):
        assert_valid_static_schema(empty_file)


def test_verify_static_schema_invalid_topology():
    """Verify that topological violations are detected statically."""
    invalid_sql = (
        "CREATE INDEX idx ON UnknownTable(col1);\n"
        "CREATE TABLE UnknownTable (col1 INT64) PRIMARY KEY (col1);"
    )
    errors = verify_static_schema(invalid_sql)
    assert len(errors) == 1
    assert "Target table 'UnknownTable' has not been declared" in errors[0]

    with pytest.raises(
        ValueError, match="Topological DDL dependency ordering violations"
    ):
        assert_valid_static_schema(invalid_sql)


def test_migrated_schema_matches_target_schema(ephemeral_database_pair):
    """Verify that baseline + sequential migrations produces an identical schema to schema_latest.sql."""
    client_migrated, client_target = ephemeral_database_pair

    diff_result = verify_migrated_schema(
        client_migrated=client_migrated,
        client_target=client_target,
        baseline_ddl_source=SCHEMA_BASELINE_SQL_PATH,
        target_ddl_source=SCHEMA_LATEST_SQL_PATH,
    )

    assert diff_result.is_match is True, (
        "Schema mismatch detected between Migrated Database and Target schema_latest.sql:\n"
        + "\n".join(f"  - {d}" for d in diff_result.differences)
    )


def test_schema_migration_idempotency(ephemeral_database_pair):
    """Verify that re-running migrations on an up-to-date database is a no-op."""
    client_migrated, _ = ephemeral_database_pair

    is_idempotent = verify_migration_idempotency(
        client_migrated=client_migrated,
        baseline_ddl_source=SCHEMA_BASELINE_SQL_PATH,
    )
    assert is_idempotent is True

