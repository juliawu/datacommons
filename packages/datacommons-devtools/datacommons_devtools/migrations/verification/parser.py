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

"""DDL file loading, sanitization, and entity extraction for Cloud Spanner migrations.

This module handles reading and pre-processing SQL schema files, stripping comments
and template tags, chunking statements, and extracting schema entity names (tables,
parent tables, secondary indexes, and Property Graph references) via regular expressions.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_GRAPH_KEYWORDS = frozenset({"AS", "SYNONYM", "NO", "DEFAULT"})


def _read_sql_source(source: str | Path) -> str:
    """Read SQL text from a file path or raw string."""
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if isinstance(source, str) and (
        source.endswith(".sql") or "\n" not in source and Path(source).is_file()
    ):
        return Path(source).read_text(encoding="utf-8")
    return str(source)


def _strip_comments_and_templates(text: str) -> str:
    """Remove Jinja block tags ({% ... %}) and SQL comments (-- ...) from text."""
    text_no_blocks = re.sub(r"\{%.*?%\}", "", text, flags=re.DOTALL)
    cleaned_lines = [re.sub(r"--.*$", "", line) for line in text_no_blocks.splitlines()]
    return "\n".join(cleaned_lines)


def _sanitize_ddl_statement(raw_stmt: str) -> str | None:
    """Sanitize a single DDL statement, returning None if empty or unrendered template."""
    stripped = raw_stmt.strip()
    if not stripped:
        return None

    # Skip unrendered template placeholders (e.g. {{ embedding_table }}) with logging
    if "{{" in stripped:
        logger.info(
            "Skipping unrendered template DDL statement: %s...",
            stripped.splitlines()[0][:60],
        )
        return None

    # The Spanner emulator does not support columnar_policy on indexes
    if "INDEX" in stripped.upper():
        stripped = re.sub(
            r"OPTIONS\s*\(\s*columnar_policy\s*=\s*'[^']*'\s*\)",
            "",
            stripped,
            flags=re.IGNORECASE,
        ).strip()

    return stripped


def load_ddl_statements(source: str | Path) -> list[str]:
    """Parse DDL statements from a SQL file or SQL string.

    Strips SQL line comments (-- ...), removes Jinja template blocks ({% ... %}),
    and splits by semicolons. Unrendered templated DDL statements containing
    placeholders (e.g. {{ embedding_table }}) are skipped with an informational log.

    Args:
        source: Path to a .sql file or a string containing DDL statements.

    Returns:
        List of non-empty DDL statement strings.
    """
    raw_text = _read_sql_source(source)
    cleaned_text = _strip_comments_and_templates(raw_text)

    statements: list[str] = []
    for raw_stmt in cleaned_text.split(";"):
        sanitized = _sanitize_ddl_statement(raw_stmt)
        if sanitized:
            statements.append(sanitized)
    return statements


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
        r"CREATE\s+(?:NULL_FILTERED\s+|UNIQUE\s+|VECTOR\s+)*INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[A-Za-z0-9_]+\s+ON\s+([A-Za-z0-9_]+)",
        stmt,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _extract_node_tables_from_graph(stmt: str) -> list[str]:
    """Extract table names defined in the NODE TABLES block of a CREATE PROPERTY GRAPH."""
    match = re.search(
        r"NODE\s+TABLES\s*\((.*?)\)\s*(?:EDGE\s+TABLES|$)",
        stmt,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    node_block = match.group(1)
    # Strip PROPERTIES(...), KEY(...), and LABEL <name>
    cleaned = re.sub(
        r"PROPERTIES\s*\([^)]*\)", "", node_block, flags=re.IGNORECASE | re.DOTALL
    )
    cleaned = re.sub(r"KEY\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"LABEL\s+[A-Za-z0-9_]+", "", cleaned, flags=re.IGNORECASE)

    node_tables: list[str] = []
    for word in re.findall(r"\b([A-Za-z0-9_]+)\b", cleaned):
        if word.upper() not in _GRAPH_KEYWORDS and word not in node_tables:
            node_tables.append(word)
    return node_tables


def _extract_edge_tables_from_graph(stmt: str) -> tuple[list[str], list[str]]:
    """Extract referenced node tables and edge tables from the EDGE TABLES block."""
    match = re.search(
        r"EDGE\s+TABLES\s*\((.*?)\)\s*(?:;|$)", stmt, re.IGNORECASE | re.DOTALL
    )
    if not match:
        return [], []

    edge_block = match.group(1)

    # 1. Extract node tables referenced in REFERENCES <table_name>
    ref_node_tables: list[str] = []
    for ref_match in re.finditer(
        r"REFERENCES\s+([A-Za-z0-9_]+)", edge_block, re.IGNORECASE
    ):
        ref_table = ref_match.group(1)
        if ref_table not in ref_node_tables:
            ref_node_tables.append(ref_table)

    # 2. Strip SOURCE KEY, DESTINATION KEY, PROPERTIES, KEY, LABEL
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
    cleaned = re.sub(r"KEY\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"LABEL\s+[A-Za-z0-9_]+", "", cleaned, flags=re.IGNORECASE)

    edge_tables: list[str] = []
    for word in re.findall(r"\b([A-Za-z0-9_]+)\b", cleaned):
        if word.upper() not in _GRAPH_KEYWORDS and word not in edge_tables:
            edge_tables.append(word)

    return ref_node_tables, edge_tables


def extract_graph_referenced_tables(stmt: str) -> tuple[list[str], list[str]]:
    """Extract node tables and edge tables referenced in CREATE PROPERTY GRAPH.

    Args:
        stmt: DDL statement string.

    Returns:
        Tuple of (node_table_names, edge_table_names).
    """
    node_tables = _extract_node_tables_from_graph(stmt)
    ref_node_tables, edge_tables = _extract_edge_tables_from_graph(stmt)

    for ref_node in ref_node_tables:
        if ref_node not in node_tables:
            node_tables.append(ref_node)

    return node_tables, edge_tables

