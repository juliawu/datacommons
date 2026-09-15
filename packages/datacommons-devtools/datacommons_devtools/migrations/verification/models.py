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

"""Schema metadata models and comparison result structures for Cloud Spanner migrations.

This module defines immutable dataclasses representing database entities extracted from
Spanner's `INFORMATION_SCHEMA` (tables, columns, constraints, indexes, and Property Graphs)
as well as the diff results produced during schema verification.
"""

import json
from dataclasses import dataclass, field
from typing import Any


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
        spanner_type: Cloud Spanner data type (e.g. 'STRING(1024)').
        is_nullable: 'YES' if nullable, 'NO' otherwise.
    """

    table_name: str
    column_name: str
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
class IndexColumnMetadata:
    """Metadata describing a column in an index.

    Attributes:
        column_name: Name of the column.
        ordinal_position: Position of column in the index key (None for STORING columns).
        column_ordering: Ordering of index column ('ASC', 'DESC', or None for STORING).
    """

    column_name: str
    ordinal_position: int | None = None
    column_ordering: str | None = None


@dataclass(frozen=True)
class IndexMetadata:
    """Metadata describing a Cloud Spanner secondary index.

    Attributes:
        table_name: Target table of the index.
        index_name: Name of the index.
        index_type: Type of index (e.g. 'INDEX', 'SEARCH', 'VECTOR').
        is_unique: True if index enforces unique constraint.
        is_null_filtered: True if index is null-filtered.
        columns: Tuple of IndexColumnMetadata instances in key order.
    """

    table_name: str
    index_name: str
    index_type: str
    is_unique: bool
    is_null_filtered: bool
    columns: tuple[IndexColumnMetadata, ...] = ()


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
        indexes: Mapping from index_name to IndexMetadata.
        property_graphs: Mapping from property_graph_name to PropertyGraphMetadata.
    """

    tables: dict[str, TableMetadata] = field(default_factory=dict)
    columns: dict[tuple[str, str], ColumnMetadata] = field(default_factory=dict)
    constraints: dict[tuple[str, str, int], ConstraintMetadata] = field(
        default_factory=dict
    )
    indexes: dict[str, IndexMetadata] = field(default_factory=dict)
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
