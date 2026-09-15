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

"""Topological dependency order validator for Cloud Spanner DDL statements and Property Graphs.

This module validates that database DDL statements execute in a sound dependency order
(base tables → interleaved tables → indexes → property graphs) so that schema creation
and migrations compile without ordering violations in Cloud Spanner.
"""

from enum import IntEnum

from datacommons_devtools.migrations.verification.parser import (
    extract_graph_referenced_tables,
    extract_parent_table_from_create,
    extract_table_name_from_create,
    extract_table_name_from_create_index,
)


class DdlDependencyLevel(IntEnum):
    """Topological dependency levels for Cloud Spanner schema definitions."""

    LEVEL_0_BASE_TABLE = 0  # Base relational tables without interleave dependencies
    LEVEL_1_EDGE_TABLE = 1  # Interleaved edge tables or tables with FK dependencies
    LEVEL_2_SECONDARY_INDEX = 2  # Secondary indexes on tables
    LEVEL_3_PROPERTY_GRAPH = 3  # Spanner Property Graph logical overlays


def _validate_create_table(stmt: str, idx: int, declared_tables: set[str]) -> list[str]:
    """Validate that an interleaved table's parent table was previously declared."""
    table_name = extract_table_name_from_create(stmt)
    parent_table = extract_parent_table_from_create(stmt)
    errors: list[str] = []

    if parent_table and parent_table not in declared_tables:
        errors.append(
            f"Statement #{idx} (CREATE TABLE {table_name}): Interleaved parent table "
            f"'{parent_table}' has not been declared before '{table_name}'."
        )

    if table_name:
        declared_tables.add(table_name)

    return errors


def _validate_create_index(stmt: str, idx: int, declared_tables: set[str]) -> list[str]:
    """Validate that the target table for a secondary/vector index was previously declared."""
    indexed_table = extract_table_name_from_create_index(stmt)
    if indexed_table and indexed_table not in declared_tables:
        return [
            f"Statement #{idx} (CREATE INDEX): Target table '{indexed_table}' "
            f"has not been declared before creating index."
        ]
    return []


def _validate_create_property_graph(
    stmt: str, idx: int, declared_tables: set[str]
) -> list[str]:
    """Validate that all node and edge tables referenced in a property graph were previously declared."""
    node_tables, edge_tables = extract_graph_referenced_tables(stmt)
    errors: list[str] = [
        f"Statement #{idx} (CREATE PROPERTY GRAPH): Referenced node table '{nt}' "
        f"has not been declared before creating property graph."
        for nt in node_tables
        if nt not in declared_tables
    ]
    errors.extend(
        [
            f"Statement #{idx} (CREATE PROPERTY GRAPH): Referenced edge table '{et}' "
            f"has not been declared before creating property graph."
            for et in edge_tables
            if et not in declared_tables
        ]
    )
    return errors


def validate_ddl_topological_order(ddl_statements: list[str]) -> list[str]:
    """Validate that a sequence of DDL statements conforms to topological dependency order.

    Rules (per DdlDependencyLevel):
    1. Base tables (DdlDependencyLevel.LEVEL_0_BASE_TABLE) must precede edge tables
       (DdlDependencyLevel.LEVEL_1_EDGE_TABLE) that interleave in them.
    2. Tables (Levels 0 & 1) must precede secondary/vector indexes
       (DdlDependencyLevel.LEVEL_2_SECONDARY_INDEX) defined on them.
    3. Node and edge tables must precede Property Graph overlays
       (DdlDependencyLevel.LEVEL_3_PROPERTY_GRAPH) referencing them.

    Args:
        ddl_statements: List of DDL statement strings in execution sequence.

    Returns:
        List of error description strings (empty if order is valid).
    """
    errors: list[str] = []
    declared_tables: set[str] = set()

    for idx, stmt in enumerate(ddl_statements, start=1):
        cleaned = stmt.strip()
        if not cleaned:
            continue

        upper_stmt = cleaned.upper()
        if upper_stmt.startswith("CREATE TABLE"):
            errors.extend(_validate_create_table(cleaned, idx, declared_tables))
        elif "INDEX" in upper_stmt and upper_stmt.startswith("CREATE"):
            errors.extend(_validate_create_index(cleaned, idx, declared_tables))
        elif "PROPERTY GRAPH" in upper_stmt:
            errors.extend(
                _validate_create_property_graph(cleaned, idx, declared_tables)
            )

    return errors


def assert_valid_ddl_topological_order(ddl_statements: list[str]) -> None:
    """Assert that a list of DDL statements satisfies topological dependency constraints.

    Args:
        ddl_statements: List of DDL statement strings in execution sequence.

    Raises:
        ValueError: If any topological dependency ordering rule is violated.
    """
    errors = validate_ddl_topological_order(ddl_statements)
    if errors:
        error_msg = (
            "Topological DDL dependency ordering violations detected:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
        raise ValueError(error_msg)
