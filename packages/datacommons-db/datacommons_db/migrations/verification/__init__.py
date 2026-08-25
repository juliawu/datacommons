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

"""Schema migration verification, comparison, and topological ordering analysis."""

from datacommons_db.migrations.verification.comparator import (
    ColumnMetadata,
    ConstraintMetadata,
    PropertyGraphMetadata,
    SchemaDiffResult,
    SchemaMetadata,
    TableMetadata,
    canonical_sort_json,
    compare_schemas,
    extract_schema_metadata,
    load_ddl_statements,
)
from datacommons_db.migrations.verification.validator import (
    DdlDependencyLevel,
    assert_valid_ddl_topological_order,
    extract_graph_referenced_tables,
    extract_parent_table_from_create,
    extract_table_name_from_create,
    extract_table_name_from_create_index,
    validate_ddl_topological_order,
)

__all__ = [
    "ColumnMetadata",
    "ConstraintMetadata",
    "DdlDependencyLevel",
    "PropertyGraphMetadata",
    "SchemaDiffResult",
    "SchemaMetadata",
    "TableMetadata",
    "assert_valid_ddl_topological_order",
    "canonical_sort_json",
    "compare_schemas",
    "extract_graph_referenced_tables",
    "extract_parent_table_from_create",
    "extract_schema_metadata",
    "extract_table_name_from_create",
    "extract_table_name_from_create_index",
    "load_ddl_statements",
    "validate_ddl_topological_order",
]
