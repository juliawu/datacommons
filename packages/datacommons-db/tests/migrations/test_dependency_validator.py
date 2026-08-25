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

"""Unit tests for dependency_validator module."""

import pytest
from datacommons_db.migrations.dependency_validator import (
    assert_valid_ddl_topological_order,
    validate_ddl_topological_order,
)


def test_validate_ddl_topological_order_valid_sequence():
    ddls = [
        "CREATE TABLE Node (subject_id STRING(1024) NOT NULL) PRIMARY KEY (subject_id)",
        (
            "CREATE TABLE Edge ("
            "subject_id STRING(1024) NOT NULL, "
            "predicate STRING(1024) NOT NULL, "
            "object_id STRING(1024) NOT NULL, "
            "provenance STRING(1024) NOT NULL"
            ") PRIMARY KEY (subject_id, predicate, object_id, provenance), "
            "INTERLEAVE IN PARENT Node ON DELETE NO ACTION"
        ),
        "CREATE INDEX InEdge ON Edge(object_id, predicate, subject_id, provenance)",
        (
            "CREATE OR REPLACE PROPERTY GRAPH DCGraph "
            "NODE TABLES(Node KEY(subject_id) LABEL Node PROPERTIES(subject_id)) "
            "EDGE TABLES(Edge KEY(subject_id, predicate, object_id, provenance) "
            "SOURCE KEY(subject_id) REFERENCES Node(subject_id) "
            "DESTINATION KEY(object_id) REFERENCES Node(subject_id))"
        ),
    ]

    errors = validate_ddl_topological_order(ddls)
    assert errors == []
    assert_valid_ddl_topological_order(ddls)


def test_validate_ddl_topological_order_interleaved_before_parent():
    ddls = [
        (
            "CREATE TABLE Edge ("
            "subject_id STRING(1024) NOT NULL, "
            "predicate STRING(1024) NOT NULL"
            ") PRIMARY KEY (subject_id, predicate), "
            "INTERLEAVE IN PARENT Node ON DELETE NO ACTION"
        ),
        "CREATE TABLE Node (subject_id STRING(1024) NOT NULL) PRIMARY KEY (subject_id)",
    ]

    errors = validate_ddl_topological_order(ddls)
    assert len(errors) == 1
    assert (
        "Interleaved parent table 'Node' has not been declared before 'Edge'"
        in errors[0]
    )

    with pytest.raises(
        ValueError, match="Topological DDL dependency ordering violations"
    ):
        assert_valid_ddl_topological_order(ddls)


def test_validate_ddl_topological_order_index_before_table():
    ddls = [
        "CREATE INDEX InEdge ON Edge(object_id)",
        "CREATE TABLE Edge (object_id STRING(1024) NOT NULL) PRIMARY KEY (object_id)",
    ]

    errors = validate_ddl_topological_order(ddls)
    assert len(errors) == 1
    assert (
        "Target table 'Edge' has not been declared before creating index" in errors[0]
    )


def test_validate_ddl_topological_order_graph_before_node_table():
    ddls = [
        (
            "CREATE PROPERTY GRAPH DCGraph "
            "NODE TABLES(Node KEY(subject_id) LABEL Node PROPERTIES(subject_id))"
        ),
        "CREATE TABLE Node (subject_id STRING(1024) NOT NULL) PRIMARY KEY (subject_id)",
    ]

    errors = validate_ddl_topological_order(ddls)
    assert len(errors) == 1
    assert (
        "Referenced node table 'Node' has not been declared before creating property graph"
        in errors[0]
    )


def test_validate_ddl_topological_order_graph_before_edge_table():
    ddls = [
        "CREATE TABLE Node (subject_id STRING(1024) NOT NULL) PRIMARY KEY (subject_id)",
        (
            "CREATE PROPERTY GRAPH DCGraph "
            "NODE TABLES(Node KEY(subject_id) LABEL Node PROPERTIES(subject_id)) "
            "EDGE TABLES(Edge KEY(subject_id) "
            "SOURCE KEY(subject_id) REFERENCES Node(subject_id) "
            "DESTINATION KEY(subject_id) REFERENCES Node(subject_id))"
        ),
        "CREATE TABLE Edge (subject_id STRING(1024) NOT NULL) PRIMARY KEY (subject_id)",
    ]

    errors = validate_ddl_topological_order(ddls)
    assert len(errors) == 1
    assert (
        "Referenced edge table 'Edge' has not been declared before creating property graph"
        in errors[0]
    )
