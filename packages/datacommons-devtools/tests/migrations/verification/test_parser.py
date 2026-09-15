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

"""Unit tests for DDL parsing and entity extraction module."""

import logging
from pathlib import Path

from datacommons_devtools.migrations.verification.parser import (
    extract_graph_referenced_tables,
    extract_parent_table_from_create,
    extract_table_name_from_create,
    extract_table_name_from_create_index,
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


def test_load_ddl_statements_skips_unrendered_template_statements(caplog):
    sql = """
    CREATE TABLE Person (
        id STRING(64) NOT NULL
    ) PRIMARY KEY (id);

    CREATE TABLE {{ embedding_table }} (
        id STRING(64) NOT NULL
    ) PRIMARY KEY (id);

    CREATE INDEX idx_person ON Person(id);
    """
    with caplog.at_level(logging.INFO):
        statements = load_ddl_statements(sql)

    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE Person")
    assert statements[1].startswith("CREATE INDEX idx_person")
    assert (
        "Skipping unrendered template DDL statement: CREATE TABLE {{ embedding_table }} ("
        in caplog.text
    )


def test_load_ddl_statements_from_path(tmp_path: Path):
    sql_file = tmp_path / "schema.sql"
    sql_file.write_text(
        "-- Comment\nCREATE TABLE Foo (id INT64) PRIMARY KEY (id);", encoding="utf-8"
    )

    statements = load_ddl_statements(sql_file)
    assert len(statements) == 1
    assert statements[0] == "CREATE TABLE Foo (id INT64) PRIMARY KEY (id)"

    # Also test passing path as string
    statements_from_str = load_ddl_statements(str(sql_file))
    assert statements_from_str == statements


def test_load_ddl_statements_normalizes_columnar_policy():
    sql = (
        "CREATE VECTOR INDEX idx ON NodeEmbedding(embeddings) "
        "OPTIONS (columnar_policy = 'COLUMNAR_POLICY_DEFAULT');"
    )
    statements = load_ddl_statements(sql)
    assert len(statements) == 1
    assert "OPTIONS" not in statements[0]
    assert statements[0] == "CREATE VECTOR INDEX idx ON NodeEmbedding(embeddings)"


def test_extract_table_name_from_create():
    assert (
        extract_table_name_from_create("CREATE TABLE Node (id STRING(64)) PRIMARY KEY (id)")
        == "Node"
    )
    assert (
        extract_table_name_from_create("CREATE TABLE IF NOT EXISTS Node (id STRING(64)) PRIMARY KEY (id)")
        == "Node"
    )
    assert extract_table_name_from_create("SELECT * FROM Node") is None


def test_extract_parent_table_from_create():
    stmt = (
        "CREATE TABLE Edge (subject_id STRING(64), predicate STRING(64)) "
        "PRIMARY KEY (subject_id, predicate), INTERLEAVE IN PARENT Node ON DELETE CASCADE"
    )
    assert extract_parent_table_from_create(stmt) == "Node"

    stmt_no_parent_kw = (
        "CREATE TABLE Edge (subject_id STRING(64)) "
        "PRIMARY KEY (subject_id), INTERLEAVE IN Node"
    )
    assert extract_parent_table_from_create(stmt_no_parent_kw) == "Node"

    assert (
        extract_parent_table_from_create("CREATE TABLE Node (id STRING(64)) PRIMARY KEY (id)")
        is None
    )


def test_extract_table_name_from_create_index_variants():
    assert (
        extract_table_name_from_create_index("CREATE INDEX idx ON MyTable(col1)")
        == "MyTable"
    )
    assert (
        extract_table_name_from_create_index("CREATE UNIQUE INDEX idx ON MyTable(col1)")
        == "MyTable"
    )
    assert (
        extract_table_name_from_create_index(
            "CREATE NULL_FILTERED INDEX idx ON MyTable(col1)"
        )
        == "MyTable"
    )
    assert (
        extract_table_name_from_create_index(
            "CREATE VECTOR INDEX idx ON NodeEmbedding(embeddings)"
        )
        == "NodeEmbedding"
    )
    assert (
        extract_table_name_from_create_index(
            "CREATE UNIQUE NULL_FILTERED INDEX IF NOT EXISTS idx ON MyTable(col1)"
        )
        == "MyTable"
    )


def test_extract_graph_referenced_tables():
    stmt = (
        "CREATE PROPERTY GRAPH DCGraph "
        "NODE TABLES(Node KEY(subject_id) LABEL Node PROPERTIES(subject_id)) "
        "EDGE TABLES(Edge KEY(subject_id, predicate) "
        "SOURCE KEY(subject_id) REFERENCES Node(subject_id) "
        "DESTINATION KEY(object_id) REFERENCES Node(subject_id))"
    )
    node_tables, edge_tables = extract_graph_referenced_tables(stmt)
    assert "Node" in node_tables
    assert "Edge" in edge_tables

