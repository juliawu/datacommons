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

"""Schema metadata extraction from Cloud Spanner INFORMATION_SCHEMA.

This module queries Cloud Spanner's `INFORMATION_SCHEMA` views to extract comprehensive
snapshots of database tables, columns, constraints, secondary indexes, and Property Graphs
into strongly-typed metadata structures for migration verification.
"""

import json
import logging

from datacommons_db.clients.spanner_client import ExecutionStatus, SpannerClient

from datacommons_devtools.migrations.verification.models import (
    ColumnMetadata,
    ConstraintMetadata,
    IndexColumnMetadata,
    IndexMetadata,
    PropertyGraphMetadata,
    SchemaMetadata,
    TableMetadata,
    canonical_sort_json,
)

logger = logging.getLogger(__name__)


def _extract_tables(spanner_client: SpannerClient) -> dict[str, TableMetadata]:
    """Query and extract table metadata from INFORMATION_SCHEMA.TABLES."""
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
    return tables


def _extract_columns(
    spanner_client: SpannerClient,
) -> dict[tuple[str, str], ColumnMetadata]:
    """Query and extract column metadata from INFORMATION_SCHEMA.COLUMNS."""
    columns_query = (
        "SELECT table_name, column_name, spanner_type, is_nullable "
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
        columns[(t_name, c_name)] = ColumnMetadata(
            table_name=t_name,
            column_name=c_name,
            spanner_type=str(row[2]),
            is_nullable=str(row[3]),
        )
    return columns


def _extract_constraints(
    spanner_client: SpannerClient,
) -> dict[tuple[str, str, int], ConstraintMetadata]:
    """Query and extract key constraint metadata from INFORMATION_SCHEMA.KEY_COLUMN_USAGE."""
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
    return constraints


def _extract_index_columns(
    spanner_client: SpannerClient,
) -> dict[str, list[IndexColumnMetadata]]:
    """Query INFORMATION_SCHEMA.INDEX_COLUMNS grouped by index name."""
    index_cols_query = (
        "SELECT table_name, index_name, column_name, ordinal_position, column_ordering "
        "FROM INFORMATION_SCHEMA.INDEX_COLUMNS "
        "WHERE table_schema = '' "
        "ORDER BY table_name, index_name, ordinal_position"
    )
    res_index_cols = spanner_client.execute_query(index_cols_query)
    if res_index_cols.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to query INFORMATION_SCHEMA.INDEX_COLUMNS: {res_index_cols.error_message}"
        )

    columns_by_index: dict[str, list[IndexColumnMetadata]] = {}
    for row in res_index_cols.rows:
        _table_name, index_name, col_name, ord_pos, col_ord = row
        col_meta = IndexColumnMetadata(
            column_name=str(col_name),
            ordinal_position=int(ord_pos) if ord_pos is not None else None,
            column_ordering=str(col_ord) if col_ord is not None else None,
        )
        columns_by_index.setdefault(str(index_name), []).append(col_meta)

    return columns_by_index


def _extract_indexes(spanner_client: SpannerClient) -> dict[str, IndexMetadata]:
    """Query and extract secondary index metadata from INFORMATION_SCHEMA."""
    # 1. Query index definitions (excluding PRIMARY_KEY)
    indexes_query = (
        "SELECT table_name, index_name, index_type, is_unique, is_null_filtered "
        "FROM INFORMATION_SCHEMA.INDEXES "
        "WHERE table_schema = '' AND index_type != 'PRIMARY_KEY' "
        "ORDER BY table_name, index_name"
    )
    res_indexes = spanner_client.execute_query(indexes_query)
    if res_indexes.status != ExecutionStatus.SUCCESS:
        raise RuntimeError(
            f"Failed to query INFORMATION_SCHEMA.INDEXES: {res_indexes.error_message}"
        )

    # 2. Query column definitions grouped by index name
    columns_by_index = _extract_index_columns(spanner_client)

    # 3. Assemble IndexMetadata with corresponding columns
    indexes: dict[str, IndexMetadata] = {}
    for row in res_indexes.rows:
        table_name, index_name, index_type, is_unique, is_null_filtered = row
        idx_cols = columns_by_index.get(str(index_name), [])

        indexes[str(index_name)] = IndexMetadata(
            table_name=str(table_name),
            index_name=str(index_name),
            index_type=str(index_type),
            is_unique=bool(is_unique),
            is_null_filtered=bool(is_null_filtered),
            columns=tuple(idx_cols),
        )

    return indexes


def _extract_property_graphs(
    spanner_client: SpannerClient,
) -> dict[str, PropertyGraphMetadata]:
    """Query and extract Property Graph definitions from INFORMATION_SCHEMA.PROPERTY_GRAPHS."""
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

    return property_graphs


def extract_schema_metadata(spanner_client: SpannerClient) -> SchemaMetadata:
    """Extract full schema metadata from INFORMATION_SCHEMA views on Spanner.

    Args:
        spanner_client: SpannerClient connected to the target database.

    Returns:
        SchemaMetadata containing tables, columns, constraints, indexes, and property graphs.

    Raises:
        RuntimeError: If querying any INFORMATION_SCHEMA view fails.
    """
    return SchemaMetadata(
        tables=_extract_tables(spanner_client),
        columns=_extract_columns(spanner_client),
        constraints=_extract_constraints(spanner_client),
        indexes=_extract_indexes(spanner_client),
        property_graphs=_extract_property_graphs(spanner_client),
    )
