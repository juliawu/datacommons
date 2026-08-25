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

"""Unit tests for schema_comparator module."""

import json
from unittest.mock import MagicMock

import pytest
from datacommons_db.clients.spanner_client import (
    ExecutionStatus,
    QueryResult,
    SpannerClient,
)
from datacommons_db.migrations.verification.comparator import (
    ColumnMetadata,
    ConstraintMetadata,
    PropertyGraphMetadata,
    SchemaMetadata,
    TableMetadata,
    canonical_sort_json,
    compare_schemas,
    extract_schema_metadata,
    load_ddl_statements,
)


def test_load_ddl_statements_parses_and_strips_comments():
    sql = """
    -- Initial comment
    CREATE TABLE Person (
        id STRING(64) NOT NULL
    ) PRIMARY KEY (id);

    -- Secondary comment
    CREATE INDEX idx_person ON Person(id);
    """
    statements = load_ddl_statements(sql)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE Person")
    assert statements[1].startswith("CREATE INDEX idx_person")


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
            "Node", "subject_id", 1, "STRING(1024)", "NO"
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


def test_compare_schemas_missing_table():
    schema_a = SchemaMetadata(tables={"Node": TableMetadata("Node", "BASE TABLE")})
    schema_b = SchemaMetadata(
        tables={
            "Node": TableMetadata("Node", "BASE TABLE"),
            "Edge": TableMetadata("Edge", "BASE TABLE"),
        }
    )

    result = compare_schemas(schema_a, schema_b, name_a="Migrated", name_b="Golden")
    assert result.is_match is False
    assert any(
        "Table 'Edge' exists in Golden but is missing in Migrated" in d
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
    cols_a = {("Node", "name"): ColumnMetadata("Node", "name", 1, "STRING(1024)", "NO")}
    cols_b = {("Node", "name"): ColumnMetadata("Node", "name", 1, "STRING(MAX)", "YES")}

    schema_a = SchemaMetadata(columns=cols_a)
    schema_b = SchemaMetadata(columns=cols_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any("type mismatch" in d for d in result.differences)
    assert any("nullability mismatch" in d for d in result.differences)


def test_compare_schemas_column_missing():
    cols_a = {("Node", "name"): ColumnMetadata("Node", "name", 1, "STRING(MAX)", "NO")}
    cols_b = {
        ("Node", "name"): ColumnMetadata("Node", "name", 1, "STRING(MAX)", "NO"),
        ("Node", "value"): ColumnMetadata("Node", "value", 2, "STRING(MAX)", "NO"),
    }

    schema_a = SchemaMetadata(columns=cols_a)
    schema_b = SchemaMetadata(columns=cols_b)

    result = compare_schemas(schema_a, schema_b)
    assert result.is_match is False
    assert any(
        "Column 'Node.value' exists in Database B (Golden) but is missing" in d
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


def test_extract_schema_metadata_with_mock_client():
    mock_client = MagicMock(spec=SpannerClient)

    # Tables query
    mock_client.execute_query.side_effect = [
        QueryResult(
            status=ExecutionStatus.SUCCESS, rows=[["Node", "BASE TABLE", None]]
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS,
            rows=[["Node", "subject_id", 1, "STRING(1024)", "NO"]],
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS, rows=[["Node", "PK_Node", "subject_id", 1]]
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS,
            rows=[["DCGraph", json.dumps({"nodes": ["Node"]})]],
        ),
    ]

    schema = extract_schema_metadata(mock_client)
    assert "Node" in schema.tables
    assert ("Node", "subject_id") in schema.columns
    assert ("Node", "PK_Node", 1) in schema.constraints
    assert "DCGraph" in schema.property_graphs
    assert schema.property_graphs["DCGraph"].metadata == {"nodes": ["Node"]}


def test_extract_schema_metadata_query_failure_raises():
    mock_client = MagicMock(spec=SpannerClient)
    mock_client.execute_query.return_value = QueryResult(
        status=ExecutionStatus.ERROR, error_message="Database offline"
    )

    with pytest.raises(RuntimeError, match="Failed to query INFORMATION_SCHEMA.TABLES"):
        extract_schema_metadata(mock_client)
