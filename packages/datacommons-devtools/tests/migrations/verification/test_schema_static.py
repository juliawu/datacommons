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

"""Static schema validation tests (no Spanner emulator required).

Verifies that schema_latest.sql and schema_baseline.sql exist, are non-empty,
and satisfy Spanner DDL topological dependency constraints.
"""

from datacommons_devtools.migrations.verification.comparator import load_ddl_statements
from datacommons_devtools.migrations.verification.validator import (
    assert_valid_ddl_topological_order,
)
from .conftest import SCHEMA_BASELINE_SQL_PATH, SCHEMA_LATEST_SQL_PATH


def test_latest_schema_exists_and_is_non_empty():
    """Verify that schema_latest.sql exists and contains valid DDL statements."""
    assert (
        SCHEMA_LATEST_SQL_PATH.is_file()
    ), f"Missing target schema file at {SCHEMA_LATEST_SQL_PATH}"
    statements = load_ddl_statements(SCHEMA_LATEST_SQL_PATH)
    assert len(statements) >= 4, (
        f"schema_latest.sql should contain at least 4 DDL statements, found {len(statements)}"
    )


def test_baseline_schema_exists_and_is_non_empty():
    """Verify that schema_baseline.sql exists and contains valid DDL statements."""
    assert (
        SCHEMA_BASELINE_SQL_PATH.is_file()
    ), f"Missing baseline schema file at {SCHEMA_BASELINE_SQL_PATH}"
    statements = load_ddl_statements(SCHEMA_BASELINE_SQL_PATH)
    assert len(statements) >= 3, (
        f"schema_baseline.sql should contain at least 3 DDL statements, found {len(statements)}"
    )


def test_latest_schema_ddl_topological_ordering():
    """Verify that schema_latest.sql satisfies topological dependency constraints."""
    statements = load_ddl_statements(SCHEMA_LATEST_SQL_PATH)
    assert_valid_ddl_topological_order(statements)


def test_baseline_schema_ddl_topological_ordering():
    """Verify that schema_baseline.sql satisfies topological dependency constraints."""
    statements = load_ddl_statements(SCHEMA_BASELINE_SQL_PATH)
    assert_valid_ddl_topological_order(statements)

