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

"""Schema comparison and INFORMATION_SCHEMA metadata extraction for Spanner migrations."""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datacommons_db.clients.spanner_client import ExecutionStatus, SpannerClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableMetadata:
    """Metadata describing a table in the database.

    Attributes:
        table_name: Name of the table.
        table_type: Type of table (e.g. 'BASE TABLE').
        parent_table_name: Parent table name if interleaved, None otherwise.
    """

    table_name: str
    table_type: str
    parent_table_name: str | None = None


@dataclass(frozen=True)
class ColumnMetadata:
    """Metadata describing a column in a table.

    Attributes:
        table_name: Table to which the column belongs.
        column_name: Name of the column.
        ordinal_position: Position of column in table definition.
        spanner_type: Cloud Spanner data type (e.g. 'STRING(1024)').
        is_nullable: 'YES' if nullable, 'NO' otherwise.
    """

    table_name: str
    column_name: str
    ordinal_position: int
    spanner_type: str
    is_nullable: str


@dataclass(frozen=True)
class ConstraintMetadata:
    """Metadata describing a key constraint or foreign key column usage.

    Attributes:
        table_name: Table with the constraint.
        constraint_name: Name of the constraint.
        column_name: Column referenced by the constraint.
        ordinal_position: Ordinal position of the column in the constraint key.
    """

    table_name: str
    constraint_name: str
    column_name: str
    ordinal_position: int


@dataclass(frozen=True)
class PropertyGraphMetadata:
    """Metadata describing a Spanner Property Graph definition.

    Attributes:
        property_graph_name: Name of the property graph (e.g. 'DCGraph').
        metadata: Parsed JSON dictionary of the property graph descriptor.
    """

    property_graph_name: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SchemaMetadata:
    """Complete snapshot of database schema extracted from INFORMATION_SCHEMA.

    Attributes:
        tables: Mapping from table_name to TableMetadata.
        columns: Mapping from (table_name, column_name) to ColumnMetadata.
        constraints: Mapping from (table_name, constraint_name, ordinal_position) to ConstraintMetadata.
        property_graphs: Mapping from property_graph_name to PropertyGraphMetadata.
    """

    tables: dict[str, TableMetadata] = field(default_factory=dict)
    columns: dict[tuple[str, str], ColumnMetadata] = field(default_factory=dict)
    constraints: dict[tuple[str, str, int], ConstraintMetadata] = field(
        default_factory=dict
    )
    property_graphs: dict[str, PropertyGraphMetadata] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaDiffResult:
    """Result of comparing two SchemaMetadata instances.

    Attributes:
        is_match: True if schemas are identical, False if differences were detected.
        differences: Human-readable list of detected differences.
    """

    is_match: bool
    differences: list[str] = field(default_factory=list)


def load_ddl_statements(source: str | Path) -> list[str]:
    """Parse DDL statements from a SQL file or SQL string.

    Strips SQL line comments (-- ...) and splits by semicolons.

    Args:
        source: Path to a .sql file or a string containing DDL statements.

    Returns:
        List of non-empty DDL statement strings.
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    elif isinstance(source, str) and (
        source.endswith(".sql") or "\n" not in source and Path(source).is_file()
    ):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = str(source)

    # Strip Jinja template blocks ({% ... %})
    text = re.sub(r"\{%.*?%\}", "", text, flags=re.DOTALL)

    # Strip single-line SQL comments (-- ...)
    cleaned_lines = []
    for line in text.splitlines():
        line_without_comment = re.sub(r"--.*$", "", line)
        cleaned_lines.append(line_without_comment)
    cleaned_text = "\n".join(cleaned_lines)

    # Split by semicolon delimiter
    raw_statements = cleaned_text.split(";")
    statements = []
    for stmt in raw_statements:
        stripped = stmt.strip()
        # Ignore empty statements and unrendered template placeholders (e.g. {{ embedding_table }})
        if stripped and "{{" not in stripped:
            # The Spanner emulator does not support columnar_policy on indexes
            if "INDEX" in stripped.upper():
                stripped = re.sub(
                    r"OPTIONS\s*\(\s*columnar_policy\s*=\s*'[^']*'\s*\)",
                    "",
                    stripped,
                    flags=re.IGNORECASE,
                ).strip()
            statements.append(stripped)
    return statements


def canonical_sort_json(obj: object) -> object:
    """Recursively sort dictionary keys and list elements for deterministic comparison.

    Args:
        obj: Arbitrary Python object from JSON deserialization.

    Returns:
        Canonical sorted representation.
    """
    if isinstance(obj, dict):
        return {k: canonical_sort_json(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        sorted_items = [canonical_sort_json(x) for x in obj]
        try:
            return sorted(sorted_items, key=lambda x: json.dumps(x, sort_keys=True))
        except TypeError:
            return sorted_items
    return obj


def extract_schema_metadata(spanner_client: SpannerClient) -> SchemaMetadata:
    """Extract full schema metadata from INFORMATION_SCHEMA views on Spanner.

    Args:
        spanner_client: SpannerClient connected to the target database.

    Returns:
        SchemaMetadata containing tables, columns, constraints, and property graphs.

    Raises:
        RuntimeError: If querying any INFORMATION_SCHEMA view fails.
    """
    # 1. Query Tables
    tables_query = (
        "SELECT table_name, table_type, parent_table_name "
        "FROM INFORMATION_SCHEMA.TABLES "
        "WHERE table_schema = '' "
        "ORDER BY table_name"
    )
    res_tables = spanner_client.execute_query(tables_query)
    if res_tables.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to query INFORMATION_SCHEMA.TABLES: {res_tables.error_message}"
        )

    tables: dict[str, TableMetadata] = {}
    for row in res_tables.rows:
        t_name, t_type, parent = str(row[0]), str(row[1]), row[2]
        tables[t_name] = TableMetadata(
            table_name=t_name,
            table_type=t_type,
            parent_table_name=str(parent) if parent is not None else None,
        )

    # 2. Query Columns
    columns_query = (
        "SELECT table_name, column_name, ordinal_position, spanner_type, is_nullable "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE table_schema = '' "
        "ORDER BY table_name, column_name"
    )
    res_cols = spanner_client.execute_query(columns_query)
    if res_cols.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to query INFORMATION_SCHEMA.COLUMNS: {res_cols.error_message}"
        )

    columns: dict[tuple[str, str], ColumnMetadata] = {}
    for row in res_cols.rows:
        t_name, c_name = str(row[0]), str(row[1])
        ord_pos = int(row[2])
        sp_type = str(row[3])
        nullable = str(row[4])
        columns[(t_name, c_name)] = ColumnMetadata(
            table_name=t_name,
            column_name=c_name,
            ordinal_position=ord_pos,
            spanner_type=sp_type,
            is_nullable=nullable,
        )

    # 3. Query Constraints & Key Column Usages
    constraints_query = (
        "SELECT table_name, constraint_name, column_name, ordinal_position "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        "WHERE table_schema = '' "
        "ORDER BY table_name, constraint_name, ordinal_position"
    )
    res_constraints = spanner_client.execute_query(constraints_query)
    if res_constraints.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to query INFORMATION_SCHEMA.KEY_COLUMN_USAGE: {res_constraints.error_message}"
        )

    constraints: dict[tuple[str, str, int], ConstraintMetadata] = {}
    for row in res_constraints.rows:
        t_name, c_name, col_name = str(row[0]), str(row[1]), str(row[2])
        ord_pos = int(row[3])
        constraints[(t_name, c_name, ord_pos)] = ConstraintMetadata(
            table_name=t_name,
            constraint_name=c_name,
            column_name=col_name,
            ordinal_position=ord_pos,
        )

    # 4. Query Spanner Property Graphs
    property_graphs: dict[str, PropertyGraphMetadata] = {}
    graph_query = (
        "SELECT property_graph_name, property_graph_metadata_json "
        "FROM INFORMATION_SCHEMA.PROPERTY_GRAPHS "
        "WHERE property_graph_schema = '' "
        "ORDER BY property_graph_name"
    )
    res_graphs = spanner_client.execute_query(graph_query)
    if res_graphs.status == ExecutionStatus.SUCCESS:
        for row in res_graphs.rows:
            pg_name = str(row[0])
            raw_meta = row[1]
            if isinstance(raw_meta, str):
                try:
                    parsed_meta = json.loads(raw_meta)
                except json.JSONDecodeError:
                    parsed_meta = {"raw": raw_meta}
            elif isinstance(raw_meta, dict):
                parsed_meta = raw_meta
            else:
                parsed_meta = {}
            property_graphs[pg_name] = PropertyGraphMetadata(
                property_graph_name=pg_name,
                metadata=canonical_sort_json(parsed_meta),
            )
    elif "not found" not in str(res_graphs.error_message).lower():
        # Only log if error is not due to view absence on older emulator versions
        logger.warning(
            "Could not query INFORMATION_SCHEMA.PROPERTY_GRAPHS: %s",
            res_graphs.error_message,
        )

    return SchemaMetadata(
        tables=tables,
        columns=columns,
        constraints=constraints,
        property_graphs=property_graphs,
    )


def compare_schemas(
    schema_a: SchemaMetadata,
    schema_b: SchemaMetadata,
    name_a: str = "Database A (Migrated)",
    name_b: str = "Database B (Golden)",
) -> SchemaDiffResult:
    """Perform a deep canonical comparison between two SchemaMetadata instances.

    Args:
        schema_a: Migrated database schema state.
        schema_b: Target golden database schema state.
        name_a: Display name for Database A in diff reports.
        name_b: Display name for Database B in diff reports.

    Returns:
        SchemaDiffResult with is_match boolean and actionable difference descriptions.
    """
    diffs: list[str] = []

    # 1. Compare Tables
    tables_a = set(schema_a.tables.keys())
    tables_b = set(schema_b.tables.keys())

    missing_in_b = sorted(tables_a - tables_b)
    missing_in_a = sorted(tables_b - tables_a)

    diffs.extend(
        [
            f"Table '{t}' exists in {name_a} but is missing in {name_b}."
            for t in missing_in_b
        ]
    )
    diffs.extend(
        [
            f"Table '{t}' exists in {name_b} but is missing in {name_a}."
            for t in missing_in_a
        ]
    )

    common_tables = sorted(tables_a & tables_b)
    for t in common_tables:
        ta = schema_a.tables[t]
        tb = schema_b.tables[t]
        if ta.table_type != tb.table_type:
            diffs.append(
                f"Table '{t}' type mismatch: '{ta.table_type}' in {name_a} vs '{tb.table_type}' in {name_b}."
            )
        if ta.parent_table_name != tb.parent_table_name:
            diffs.append(
                f"Table '{t}' parent mismatch: '{ta.parent_table_name}' in {name_a} vs '{tb.parent_table_name}' in {name_b}."
            )

    # 2. Compare Columns
    cols_a = set(schema_a.columns.keys())
    cols_b = set(schema_b.columns.keys())

    missing_cols_in_b = sorted(cols_a - cols_b)
    missing_cols_in_a = sorted(cols_b - cols_a)

    diffs.extend(
        [
            f"Column '{t_name}.{c_name}' exists in {name_a} but is missing in {name_b}."
            for t_name, c_name in missing_cols_in_b
        ]
    )
    diffs.extend(
        [
            f"Column '{t_name}.{c_name}' exists in {name_b} but is missing in {name_a}."
            for t_name, c_name in missing_cols_in_a
        ]
    )

    common_cols = sorted(cols_a & cols_b)
    for key in common_cols:
        ca = schema_a.columns[key]
        cb = schema_b.columns[key]
        t_name, c_name = key

        if ca.spanner_type.upper() != cb.spanner_type.upper():
            diffs.append(
                f"Column '{t_name}.{c_name}' type mismatch: '{ca.spanner_type}' in {name_a} vs '{cb.spanner_type}' in {name_b}."
            )
        if ca.is_nullable.upper() != cb.is_nullable.upper():
            diffs.append(
                f"Column '{t_name}.{c_name}' nullability mismatch: '{ca.is_nullable}' in {name_a} vs '{cb.is_nullable}' in {name_b}."
            )
        if ca.ordinal_position != cb.ordinal_position:
            diffs.append(
                f"Column '{t_name}.{c_name}' ordinal position mismatch: {ca.ordinal_position} in {name_a} vs {cb.ordinal_position} in {name_b}."
            )

    # 3. Compare Constraints & Key Column Usages
    const_a = set(schema_a.constraints.keys())
    const_b = set(schema_b.constraints.keys())

    missing_const_in_b = sorted(const_a - const_b)
    missing_const_in_a = sorted(const_b - const_a)

    diffs.extend(
        [
            f"Constraint '{c_name}' (pos {pos}) on table '{t_name}' exists in {name_a} but is missing in {name_b}."
            for t_name, c_name, pos in missing_const_in_b
        ]
    )
    diffs.extend(
        [
            f"Constraint '{c_name}' (pos {pos}) on table '{t_name}' exists in {name_b} but is missing in {name_a}."
            for t_name, c_name, pos in missing_const_in_a
        ]
    )

    common_const = sorted(const_a & const_b)
    for key in common_const:
        cta = schema_a.constraints[key]
        ctb = schema_b.constraints[key]
        t_name, c_name, pos = key
        if cta.column_name != ctb.column_name:
            diffs.append(
                f"Constraint '{c_name}' (pos {pos}) on table '{t_name}' column mismatch: '{cta.column_name}' in {name_a} vs '{ctb.column_name}' in {name_b}."
            )

    # 4. Compare Property Graphs
    pg_a = set(schema_a.property_graphs.keys())
    pg_b = set(schema_b.property_graphs.keys())

    missing_pg_in_b = sorted(pg_a - pg_b)
    missing_pg_in_a = sorted(pg_b - pg_a)

    diffs.extend(
        [
            f"Property Graph '{g_name}' exists in {name_a} but is missing in {name_b}."
            for g_name in missing_pg_in_b
        ]
    )
    diffs.extend(
        [
            f"Property Graph '{g_name}' exists in {name_b} but is missing in {name_a}."
            for g_name in missing_pg_in_a
        ]
    )

    common_pg = sorted(pg_a & pg_b)
    for g_name in common_pg:
        meta_a = schema_a.property_graphs[g_name].metadata
        meta_b = schema_b.property_graphs[g_name].metadata
        if meta_a != meta_b:
            json_a = json.dumps(meta_a, sort_keys=True, indent=2)
            json_b = json.dumps(meta_b, sort_keys=True, indent=2)
            diffs.append(
                f"Property Graph '{g_name}' descriptor metadata mismatch:\n"
                f"--- {name_a} ---\n{json_a}\n"
                f"--- {name_b} ---\n{json_b}"
            )

    return SchemaDiffResult(is_match=len(diffs) == 0, differences=diffs)
