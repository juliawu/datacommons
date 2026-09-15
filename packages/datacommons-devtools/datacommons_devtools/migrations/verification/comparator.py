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

"""Deep structural schema comparison and canonical diff engine for Cloud Spanner migrations.

This module performs deterministic structural diffs between two `SchemaMetadata` snapshots.
It compares tables, columns, key constraints, secondary/vector indexes, and Property Graph
descriptors to prove that a migrated database strictly matches its target baseline schema.
"""

import json

from datacommons_devtools.migrations.verification.models import (
    ColumnMetadata,
    ConstraintMetadata,
    IndexMetadata,
    PropertyGraphMetadata,
    SchemaDiffResult,
    SchemaMetadata,
    TableMetadata,
)


def _compare_tables(
    tables_a: dict[str, TableMetadata],
    tables_b: dict[str, TableMetadata],
    name_a: str,
    name_b: str,
) -> list[str]:
    """Compare table presence, types, and parent interleaving."""
    diffs: list[str] = []
    keys_a = set(tables_a.keys())
    keys_b = set(tables_b.keys())

    missing_in_b = sorted(keys_a - keys_b)
    missing_in_a = sorted(keys_b - keys_a)

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

    common_tables = sorted(keys_a & keys_b)
    for t in common_tables:
        ta = tables_a[t]
        tb = tables_b[t]
        if ta.table_type != tb.table_type:
            diffs.append(
                f"Table '{t}' type mismatch: '{ta.table_type}' in {name_a} vs '{tb.table_type}' in {name_b}."
            )
        if ta.parent_table_name != tb.parent_table_name:
            diffs.append(
                f"Table '{t}' parent mismatch: '{ta.parent_table_name}' in {name_a} vs '{tb.parent_table_name}' in {name_b}."
            )

    return diffs


def _compare_columns(
    cols_a: dict[tuple[str, str], ColumnMetadata],
    cols_b: dict[tuple[str, str], ColumnMetadata],
    name_a: str,
    name_b: str,
) -> list[str]:
    """Compare column existence, types, and nullability (ignoring physical ordinal position)."""
    diffs: list[str] = []
    keys_a = set(cols_a.keys())
    keys_b = set(cols_b.keys())

    missing_cols_in_b = sorted(keys_a - keys_b)
    missing_cols_in_a = sorted(keys_b - keys_a)

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

    common_cols = sorted(keys_a & keys_b)
    for key in common_cols:
        ca = cols_a[key]
        cb = cols_b[key]
        t_name, c_name = key

        if ca.spanner_type.upper() != cb.spanner_type.upper():
            diffs.append(
                f"Column '{t_name}.{c_name}' type mismatch: '{ca.spanner_type}' in {name_a} vs '{cb.spanner_type}' in {name_b}."
            )
        if ca.is_nullable.upper() != cb.is_nullable.upper():
            diffs.append(
                f"Column '{t_name}.{c_name}' nullability mismatch: '{ca.is_nullable}' in {name_a} vs '{cb.is_nullable}' in {name_b}."
            )

    return diffs


def _compare_constraints(
    const_a: dict[tuple[str, str, int], ConstraintMetadata],
    const_b: dict[tuple[str, str, int], ConstraintMetadata],
    name_a: str,
    name_b: str,
) -> list[str]:
    """Compare primary key and key column constraints."""
    diffs: list[str] = []
    keys_a = set(const_a.keys())
    keys_b = set(const_b.keys())

    missing_const_in_b = sorted(keys_a - keys_b)
    missing_const_in_a = sorted(keys_b - keys_a)

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

    common_const = sorted(keys_a & keys_b)
    for key in common_const:
        cta = const_a[key]
        ctb = const_b[key]
        t_name, c_name, pos = key
        if cta.column_name != ctb.column_name:
            diffs.append(
                f"Constraint '{c_name}' (pos {pos}) on table '{t_name}' column mismatch: '{cta.column_name}' in {name_a} vs '{ctb.column_name}' in {name_b}."
            )

    return diffs


def _compare_indexes(
    idx_a: dict[str, IndexMetadata],
    idx_b: dict[str, IndexMetadata],
    name_a: str,
    name_b: str,
) -> list[str]:
    """Compare secondary/vector index existence, target tables, uniqueness, null-filtering, and columns."""
    diffs: list[str] = []
    keys_a = set(idx_a.keys())
    keys_b = set(idx_b.keys())

    missing_idx_in_b = sorted(keys_a - keys_b)
    missing_idx_in_a = sorted(keys_b - keys_a)

    diffs.extend(
        [
            f"Index '{idx}' on table '{idx_a[idx].table_name}' exists in {name_a} but is missing in {name_b}."
            for idx in missing_idx_in_b
        ]
    )
    diffs.extend(
        [
            f"Index '{idx}' on table '{idx_b[idx].table_name}' exists in {name_b} but is missing in {name_a}."
            for idx in missing_idx_in_a
        ]
    )

    common_idx = sorted(keys_a & keys_b)
    for idx in common_idx:
        ia = idx_a[idx]
        ib = idx_b[idx]

        if ia.table_name != ib.table_name:
            diffs.append(
                f"Index '{idx}' target table mismatch: '{ia.table_name}' in {name_a} vs '{ib.table_name}' in {name_b}."
            )
        if ia.index_type != ib.index_type:
            diffs.append(
                f"Index '{idx}' type mismatch: '{ia.index_type}' in {name_a} vs '{ib.index_type}' in {name_b}."
            )
        if ia.is_unique != ib.is_unique:
            diffs.append(
                f"Index '{idx}' uniqueness mismatch: {ia.is_unique} in {name_a} vs {ib.is_unique} in {name_b}."
            )
        if ia.is_null_filtered != ib.is_null_filtered:
            diffs.append(
                f"Index '{idx}' null-filtered mismatch: {ia.is_null_filtered} in {name_a} vs {ib.is_null_filtered} in {name_b}."
            )
        if ia.columns != ib.columns:
            diffs.append(
                f"Index '{idx}' columns mismatch: {ia.columns} in {name_a} vs {ib.columns} in {name_b}."
            )

    return diffs


def _compare_property_graphs(
    pg_a: dict[str, PropertyGraphMetadata],
    pg_b: dict[str, PropertyGraphMetadata],
    name_a: str,
    name_b: str,
) -> list[str]:
    """Compare Property Graph existence and descriptor metadata."""
    diffs: list[str] = []
    keys_a = set(pg_a.keys())
    keys_b = set(pg_b.keys())

    missing_pg_in_b = sorted(keys_a - keys_b)
    missing_pg_in_a = sorted(keys_b - keys_a)

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

    common_pg = sorted(keys_a & keys_b)
    for g_name in common_pg:
        meta_a = pg_a[g_name].metadata
        meta_b = pg_b[g_name].metadata
        if meta_a != meta_b:
            json_a = json.dumps(meta_a, sort_keys=True, indent=2)
            json_b = json.dumps(meta_b, sort_keys=True, indent=2)
            diffs.append(
                f"Property Graph '{g_name}' descriptor metadata mismatch:\n"
                f"--- {name_a} ---\n{json_a}\n"
                f"--- {name_b} ---\n{json_b}"
            )

    return diffs


def compare_schemas(
    schema_a: SchemaMetadata,
    schema_b: SchemaMetadata,
    name_a: str = "Database A (Migrated)",
    name_b: str = "Database B (Target)",
) -> SchemaDiffResult:
    """Perform a deep canonical comparison between two SchemaMetadata instances.

    Args:
        schema_a: Migrated database schema state.
        schema_b: Target database schema state.
        name_a: Display name for Database A in diff reports.
        name_b: Display name for Database B in diff reports.

    Returns:
        SchemaDiffResult with is_match boolean and actionable difference descriptions.
    """
    diffs: list[str] = []
    diffs.extend(_compare_tables(schema_a.tables, schema_b.tables, name_a, name_b))
    diffs.extend(_compare_columns(schema_a.columns, schema_b.columns, name_a, name_b))
    diffs.extend(
        _compare_constraints(schema_a.constraints, schema_b.constraints, name_a, name_b)
    )
    diffs.extend(_compare_indexes(schema_a.indexes, schema_b.indexes, name_a, name_b))
    diffs.extend(
        _compare_property_graphs(
            schema_a.property_graphs, schema_b.property_graphs, name_a, name_b
        )
    )

    return SchemaDiffResult(is_match=len(diffs) == 0, differences=diffs)
