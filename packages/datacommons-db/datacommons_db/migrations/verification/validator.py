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

"""Topological dependency order validator for Spanner DDL statements and Property Graphs."""

import re
from enum import IntEnum


class DdlDependencyLevel(IntEnum):
    """Topological dependency levels for Cloud Spanner schema definitions."""

    LEVEL_0_BASE_TABLE = 0  # Base relational tables without interleave dependencies
    LEVEL_1_EDGE_TABLE = 1  # Interleaved edge tables or tables with FK dependencies
    LEVEL_2_SECONDARY_INDEX = 2  # Secondary indexes on tables
    LEVEL_3_PROPERTY_GRAPH = 3  # Spanner Property Graph logical overlays


def extract_table_name_from_create(stmt: str) -> str | None:
    """Extract table name from CREATE TABLE statement."""
    match = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_]+)",
        stmt,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def extract_parent_table_from_create(stmt: str) -> str | None:
    """Extract parent table name from INTERLEAVE IN [PARENT] clause."""
    match = re.search(
        r"INTERLEAVE\s+IN\s+(?:PARENT\s+)?([A-Za-z0-9_]+)", stmt, re.IGNORECASE
    )
    return match.group(1) if match else None


def extract_table_name_from_create_index(stmt: str) -> str | None:
    """Extract target table name from CREATE INDEX statement."""
    match = re.search(
        r"CREATE\s+(?:NULL_FILTERED\s+|UNIQUE\s+)*INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[A-Za-z0-9_]+\s+ON\s+([A-Za-z0-9_]+)",
        stmt,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def extract_graph_referenced_tables(stmt: str) -> tuple[list[str], list[str]]:
    """Extract node tables and edge tables referenced in CREATE PROPERTY GRAPH.

    Args:
        stmt: DDL statement string.

    Returns:
        Tuple of (node_table_names, edge_table_names).
    """
    node_tables: list[str] = []
    edge_tables: list[str] = []

    # Find NODE TABLES (...) block
    node_block_match = re.search(
        r"NODE\s+TABLES\s*\((.*?)\)\s*(?:EDGE\s+TABLES|$)",
        stmt,
        re.IGNORECASE | re.DOTALL,
    )
    if node_block_match:
        node_block = node_block_match.group(1)
        # Strip PROPERTIES(...), KEY(...), and LABEL <name>
        cleaned = re.sub(
            r"PROPERTIES\s*\([^)]*\)", "", node_block, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(
            r"KEY\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(r"LABEL\s+[A-Za-z0-9_]+", "", cleaned, flags=re.IGNORECASE)
        for word in re.findall(r"\b([A-Za-z0-9_]+)\b", cleaned):
            if (
                word.upper() not in ("AS", "SYNONYM", "NO", "DEFAULT")
                and word not in node_tables
            ):
                node_tables.append(word)

    # Find EDGE TABLES (...) block
    edge_block_match = re.search(
        r"EDGE\s+TABLES\s*\((.*?)\)\s*(?:;|$)", stmt, re.IGNORECASE | re.DOTALL
    )
    if edge_block_match:
        edge_block = edge_block_match.group(1)
        # Extract referenced node tables in REFERENCES <table_name>
        for ref_match in re.finditer(
            r"REFERENCES\s+([A-Za-z0-9_]+)", edge_block, re.IGNORECASE
        ):
            ref_table = ref_match.group(1)
            if ref_table not in node_tables:
                node_tables.append(ref_table)

        # Strip SOURCE KEY, DESTINATION KEY, PROPERTIES, KEY, LABEL
        cleaned = re.sub(
            r"SOURCE\s+KEY\s*\([^)]*\)\s+REFERENCES\s+[A-Za-z0-9_]+(?:\s*\([^)]*\))?",
            "",
            edge_block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r"DESTINATION\s+KEY\s*\([^)]*\)\s+REFERENCES\s+[A-Za-z0-9_]+(?:\s*\([^)]*\))?",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r"PROPERTIES\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(
            r"KEY\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(r"LABEL\s+[A-Za-z0-9_]+", "", cleaned, flags=re.IGNORECASE)

        for word in re.findall(r"\b([A-Za-z0-9_]+)\b", cleaned):
            if (
                word.upper() not in ("AS", "SYNONYM", "NO", "DEFAULT")
                and word not in edge_tables
            ):
                edge_tables.append(word)

    return node_tables, edge_tables


def validate_ddl_topological_order(ddl_statements: list[str]) -> list[str]:
    """Validate that a sequence of DDL statements conforms to topological dependency order.

    Rules:
    1. Base node tables (Level 0) must precede edge linking tables (Level 1) that interleave in them.
    2. Tables (Levels 0 & 1) must precede secondary indexes (Level 2) defined on them.
    3. Node and edge tables must precede Property Graph overlays (Level 3) referencing them.

    Args:
        ddl_statements: List of DDL statement strings in execution sequence.

    Returns:
        List of error description strings (empty if order is valid).
    """
    errors: list[str] = []
    declared_tables: set[str] = set()
    declared_indexes: set[str] = set()

    for idx, stmt in enumerate(ddl_statements, start=1):
        cleaned = stmt.strip()
        if not cleaned:
            continue

        upper_stmt = cleaned.upper()

        # Check CREATE TABLE
        if upper_stmt.startswith("CREATE TABLE"):
            table_name = extract_table_name_from_create(cleaned)
            parent_table = extract_parent_table_from_create(cleaned)

            if parent_table and parent_table not in declared_tables:
                errors.append(
                    f"Statement #{idx} (CREATE TABLE {table_name}): Interleaved parent table "
                    f"'{parent_table}' has not been declared before '{table_name}'."
                )

            if table_name:
                declared_tables.add(table_name)

        # Check CREATE INDEX
        elif "INDEX" in upper_stmt and upper_stmt.startswith("CREATE"):
            indexed_table = extract_table_name_from_create_index(cleaned)
            if indexed_table and indexed_table not in declared_tables:
                errors.append(
                    f"Statement #{idx} (CREATE INDEX): Target table '{indexed_table}' "
                    f"has not been declared before creating index."
                )
            # Record index name if found
            idx_name_match = re.search(
                r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_]+)",
                cleaned,
                re.IGNORECASE,
            )
            if idx_name_match:
                declared_indexes.add(idx_name_match.group(1))

        # Check CREATE PROPERTY GRAPH
        elif "PROPERTY GRAPH" in upper_stmt:
            node_tables, edge_tables = extract_graph_referenced_tables(cleaned)

            errors.extend(
                [
                    f"Statement #{idx} (CREATE PROPERTY GRAPH): Referenced node table '{nt}' "
                    f"has not been declared before creating property graph."
                    for nt in node_tables
                    if nt not in declared_tables
                ]
            )

            errors.extend(
                [
                    f"Statement #{idx} (CREATE PROPERTY GRAPH): Referenced edge table '{et}' "
                    f"has not been declared before creating property graph."
                    for et in edge_tables
                    if et not in declared_tables
                ]
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
