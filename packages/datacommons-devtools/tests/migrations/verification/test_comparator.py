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

"""Unit tests for schema comparator and canonical sorting."""

from datacommons_devtools.migrations.verification.comparator import compare_schemas
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


def test_canonical_sort_json_recursively_sorts():
    data = {
        "z_key": [3, 1, 2],
        "a_key": {"beta": 2, "alpha": 1},
        "nested_list": [{"b": 2}, {"a": 1}],
    }
    sorted_data = canonical_sort_json(data)
    assert list(sorted_data.keys()) == ["a_key", "nested_list", "z_key"]
    assert list(sorted_data["a_key"].keys()) == ["alpha", "beta"]
    assert sorted_data["z_key"] == [1, 2, 3]


def test_compare_schemas_exact_match():
    tables = {"Node": TableMetadata("Node", "BASE TABLE")}
    cols = {
        ("Node", "subject_id"): ColumnMetadata(
            "Node", "subject_id", "STRING(1024)", "NO"
        )
    }
    consts = {
        ("Node", "PK_Node", 1): ConstraintMetadata("Node", "PK_Node", "subject_id", 1)
    }
    graphs = {"DCGraph": PropertyGraphMetadata("DCGraph", {"nodes": ["Node"]})}

    schema_a = SchemaMetadata(
        tables=tables, columns=cols, constraints=consts, property_graphs=graphs
    )
    schema_b = SchemaMetadata(
        tables=tables, columns=cols, constraints=consts, property_graphs=graphs
    )

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is True
    assert result.differences == []


def test_compare_schemas_different_column_order_matches():
    cols_a = {
        ("Node", "subject_id"): ColumnMetadata(
            "Node", "subject_id", "STRING(1024)", "NO"
        ),
        ("Node", "name"): ColumnMetadata("Node", "name", "STRING(MAX)", "YES"),
    }
    cols_b = {
        ("Node", "name"): ColumnMetadata("Node", "name", "STRING(MAX)", "YES"),
        ("Node", "subject_id"): ColumnMetadata(
            "Node", "subject_id", "STRING(1024)", "NO"
        ),
    }
    schema_a = SchemaMetadata(columns=cols_a)
    schema_b = SchemaMetadata(columns=cols_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is True
    assert result.differences == []


def test_compare_schemas_missing_table():
    schema_a = SchemaMetadata(tables={"Node": TableMetadata("Node", "BASE TABLE")})
    schema_b = SchemaMetadata(
        tables={
            "Node": TableMetadata("Node", "BASE TABLE"),
            "Edge": TableMetadata("Edge", "BASE TABLE"),
        }
    )

    result = compare_schemas(schema_a, schema_b, name_a="Migrated", name_b="Target")
    assert result.is_match is False
    assert any(
        "Table 'Edge' exists in Target but is missing in Migrated" in d
        for d in result.differences
    )


def test_compare_schemas_table_type_or_parent_mismatch():
    schema_a = SchemaMetadata(
        tables={"Edge": TableMetadata("Edge", "BASE TABLE", "Node")}
    )
    schema_b = SchemaMetadata(
        tables={"Edge": TableMetadata("Edge", "BASE TABLE", None)}
    )

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any("parent mismatch" in d for d in result.differences)


def test_compare_schemas_column_mismatch():
    cols_a = {("Node", "name"): ColumnMetadata("Node", "name", "STRING(1024)", "NO")}
    cols_b = {("Node", "name"): ColumnMetadata("Node", "name", "STRING(MAX)", "YES")}

    schema_a = SchemaMetadata(columns=cols_a)
    schema_b = SchemaMetadata(columns=cols_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any("type mismatch" in d for d in result.differences)
    assert any("nullability mismatch" in d for d in result.differences)


def test_compare_schemas_column_missing():
    cols_a = {("Node", "name"): ColumnMetadata("Node", "name", "STRING(MAX)", "NO")}
    cols_b = {
        ("Node", "name"): ColumnMetadata("Node", "name", "STRING(MAX)", "NO"),
        ("Node", "value"): ColumnMetadata("Node", "value", "STRING(MAX)", "NO"),
    }

    schema_a = SchemaMetadata(columns=cols_a)
    schema_b = SchemaMetadata(columns=cols_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any(
        "Column 'Node.value' exists in Database B (Target) but is missing" in d
        for d in result.differences
    )


def test_compare_schemas_constraint_mismatch():
    const_a = {
        ("Node", "PK_Node", 1): ConstraintMetadata("Node", "PK_Node", "subject_id", 1)
    }
    const_b = {("Node", "PK_Node", 1): ConstraintMetadata("Node", "PK_Node", "dcid", 1)}

    schema_a = SchemaMetadata(constraints=const_a)
    schema_b = SchemaMetadata(constraints=const_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any("column mismatch" in d for d in result.differences)


def test_compare_schemas_property_graph_missing():
    graphs_a = {"DCGraph": PropertyGraphMetadata("DCGraph", {"nodes": ["Node"]})}
    schema_a = SchemaMetadata(property_graphs=graphs_a)
    schema_b = SchemaMetadata(property_graphs={})

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any(
        "Property Graph 'DCGraph' exists in Database A (Migrated) but is missing" in d
        for d in result.differences
    )


def test_compare_schemas_property_graph_metadata_mismatch():
    graphs_a = {
        "DCGraph": PropertyGraphMetadata("DCGraph", {"nodes": ["Node", "Extra"]})
    }
    graphs_b = {"DCGraph": PropertyGraphMetadata("DCGraph", {"nodes": ["Node"]})}

    schema_a = SchemaMetadata(property_graphs=graphs_a)
    schema_b = SchemaMetadata(property_graphs=graphs_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any("descriptor metadata mismatch" in d for d in result.differences)


def test_compare_schemas_missing_index():
    cols = (
        IndexColumnMetadata(
            column_name="subject_id", ordinal_position=1, column_ordering="ASC"
        ),
    )
    idx_a = {
        "InEdge": IndexMetadata(
            table_name="Edge",
            index_name="InEdge",
            index_type="INDEX",
            is_unique=False,
            is_null_filtered=False,
            columns=cols,
        )
    }
    schema_a = SchemaMetadata(indexes=idx_a)
    schema_b = SchemaMetadata(indexes={})

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any(
        "Index 'InEdge' on table 'Edge' exists in Database A (Migrated) but is missing"
        in d
        for d in result.differences
    )


def test_compare_schemas_index_property_or_column_mismatch():
    cols_a = (
        IndexColumnMetadata(
            column_name="subject_id", ordinal_position=1, column_ordering="ASC"
        ),
    )
    cols_b = (
        IndexColumnMetadata(
            column_name="subject_id", ordinal_position=1, column_ordering="DESC"
        ),
    )
    idx_a = {
        "InEdge": IndexMetadata(
            table_name="Edge",
            index_name="InEdge",
            index_type="INDEX",
            is_unique=False,
            is_null_filtered=False,
            columns=cols_a,
        )
    }
    idx_b = {
        "InEdge": IndexMetadata(
            table_name="Edge",
            index_name="InEdge",
            index_type="INDEX",
            is_unique=True,
            is_null_filtered=True,
            columns=cols_b,
        )
    }
    schema_a = SchemaMetadata(indexes=idx_a)
    schema_b = SchemaMetadata(indexes=idx_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any("uniqueness mismatch" in d for d in result.differences)
    assert any("null-filtered mismatch" in d for d in result.differences)
    assert any("columns mismatch" in d for d in result.differences)

