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

"""Unit tests for schema metadata extractor module."""

import json
from unittest.mock import MagicMock

import pytest
from datacommons_db.clients.spanner_client import (
    ExecutionStatus,
    QueryResult,
    SpannerClient,
)
from datacommons_devtools.migrations.verification.extractor import (
    extract_schema_metadata,
)


def test_extract_schema_metadata_with_mock_client():
    mock_client = MagicMock(spec=SpannerClient)

    # Tables query, Columns query, Constraints query, Indexes query, Index columns query, Property graphs query
    mock_client.execute_query.side_effect = [
        QueryResult(
            status=ExecutionStatus.SUCCESS, rows=[["Node", "BASE TABLE", None]]
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS,
            rows=[["Node", "subject_id", "STRING(1024)", "NO"]],
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS, rows=[["Node", "PK_Node", "subject_id", 1]]
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS,
            rows=[["Edge", "InEdge", "INDEX", False, False]],
        ),
        QueryResult(
            status=ExecutionStatus.SUCCESS,
            rows=[["Edge", "InEdge", "object_id", 1, "ASC"]],
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
    assert "InEdge" in schema.indexes
    assert schema.indexes["InEdge"].table_name == "Edge"
    assert len(schema.indexes["InEdge"].columns) == 1
    assert schema.indexes["InEdge"].columns[0].column_name == "object_id"
    assert "DCGraph" in schema.property_graphs
    assert schema.property_graphs["DCGraph"].metadata == {"nodes": ["Node"]}


def test_extract_schema_metadata_query_failure_raises():
    mock_client = MagicMock(spec=SpannerClient)
    mock_client.execute_query.return_value = QueryResult(
        status=ExecutionStatus.ERROR, error_message="Database offline"
    )

    with pytest.raises(RuntimeError, match="Failed to query INFORMATION_SCHEMA.TABLES"):
        extract_schema_metadata(mock_client)

