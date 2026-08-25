# Data Commons Schema Migrations Developer Guide

This guide details how developers create, update, and manage Cloud Spanner database schema migrations for the Data Commons Platform using the `manage-migrations` devOps CLI tool.

---

## 1. Overview & Architecture

Data Commons uses Google Cloud Spanner as its relational graph store. Schema migrations live in [`packages/datacommons-db/datacommons_db/migrations/migration_scripts/`](../packages/datacommons-db/datacommons_db/migrations/migration_scripts/) and are executed in strict chronological order based on their UTC timestamps.

### Key Distinction: Authoring vs Execution

| Tool | Purpose | Target |
| :--- | :--- | :--- |
| **`manage-migrations`** | **Developer Authoring Tool**: Scaffolds boilerplate, updates timestamps, and resolves merge conflicts. | Local migration files on disk |
| **`datacommons admin migrate-db`** | **Database Execution Engine**: Connects to Cloud Spanner, queries applied migrations, and executes unapplied `upgrade()` methods. | Live Cloud Spanner instance / emulator |

---

## 2. Migration Script Conventions

Every migration script follows standard repository conventions:

1. **Filename Format:** `YYYYMMDDHHMMSS_<change_name>.py` (14-digit UTC timestamp prefix + snake_case change name).
   - Example: `20260819135412_add_observation_indexes.py`
2. **Base Class:** Subclass of `SchemaMigration` ([`datacommons_db.migrations.base.SchemaMigration`](../packages/datacommons-db/datacommons_db/migrations/base.py)).
3. **Class Attributes:**
   - `description: str`: Human-readable summary of the schema change.
   - `creation_timestamp: str`: UTC ISO-8601 timestamp string (`YYYY-MM-DDTHH:MM:SSZ`) matching the filename prefix.
4. **Upgrade Method:**
   - `def upgrade(self, spanner_client: SpannerClient) -> None`: Contains the DDL or DML logic to apply the schema migration.

---

## 3. Using `manage-migrations`

The `manage-migrations` CLI is registered as a workspace command in [`pyproject.toml`](../pyproject.toml). You can run it with `uv run manage-migrations <command>` or directly as `manage-migrations <command>` if your virtual environment is active.

### Creating a New Migration (`create`)

To generate a new timestamped migration script with boilerplate pre-filled:

```bash
uv run manage-migrations create <change_name> [-d/--description "<description>"]
```

#### Examples:

```bash
# Basic creation (description is derived from change name)
uv run manage-migrations create add_node_tables

# Creation with explicit description
uv run manage-migrations create add_edge_indexes -d "Add composite index on Edge object_value and predicate"
```

#### What this does:
1. Sanitizes `<change_name>` to lowercase `snake_case`.
2. Generates the current UTC timestamp (e.g. `20260819173000` and `2026-08-19T17:30:00Z`).
3. Creates `packages/datacommons-db/datacommons_db/migrations/migration_scripts/20260819173000_add_edge_indexes.py`.
4. Populates the file with the Apache 2.0 license header, typed `Migration` class, and empty `upgrade()` method.

---

## 4. Writing Migration Logic

Open the generated migration file and implement the `upgrade()` method using [`SpannerClient`](../packages/datacommons-db/datacommons_db/clients/spanner_client.py):

```python
from datacommons_db.clients.spanner_client import ExecutionStatus, SpannerClient
from datacommons_db.migrations.base import SchemaMigration


class Migration(SchemaMigration):

    description: str = "Add composite index on Edge object_value and predicate"
    creation_timestamp: str = "2026-08-19T17:35:10Z"

    def upgrade(self, spanner_client: SpannerClient) -> None:
        """Executes forward schema changes to upgrade the database."""
        result = spanner_client.execute_ddl([
            """
            CREATE INDEX EdgeByObjectValueAndPredicate 
            ON Edges(object_value, predicate)
            """
        ])
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(f"Failed to apply migration: {result.error_message}")
```

### Best Practices:
- **Forward-Only DDL:** Cloud Spanner migrations should execute forward DDL statements (`CREATE TABLE`, `CREATE INDEX`, `ALTER TABLE`).
- **Idempotency & Safety:** Check table/index existence or structure when applicable before destructive changes.
- **Always Check `ExecutionStatus`:** Verify `result.status == ExecutionStatus.SUCCESS` and raise a `RuntimeError` on failure to trigger rollback/abort.

---

## 5. Golden `schema.sql` Synchronization

Whenever you add or modify a migration script, you **must also update the golden `schema.sql` file** ([`packages/datacommons-db/datacommons_db/migrations/schema.sql`](../packages/datacommons-db/datacommons_db/migrations/schema.sql)).

`schema.sql` serves as the authoritative single source of truth for the complete database schema state. Automated CI tests will verify that applying all sequential migrations to a fresh database produces a schema strictly identical to executing `schema.sql` directly.

---

## 6. Verification & Testing

Always verify that your migration script conforms to repository standards and passes automated tests:

### 1. Validate Migration Script Syntax & Metadata
Ensures your script follows the `YYYYMMDDHHMMSS_<change_name>.py` naming convention, has unique UTC ISO-8601 timestamps, and implements `upgrade()`:
```bash
uv run pytest packages/datacommons-db/tests/migrations/test_migration_scripts.py
```

### 2. Validate Topological DDL Ordering & Comparator
Verifies that statements conform to Spanner's 4-level compilation order (Base Tables → Edge Tables → Secondary Indexes → Property Graphs):
```bash
uv run pytest packages/datacommons-db/tests/migrations/test_dependency_validator.py packages/datacommons-db/tests/migrations/test_schema_comparator.py
```

### 3. Run End-to-End Migration Test (Cloud Spanner Emulator)
Executes the dual-database migration verification test (`db_migrated` vs. `db_golden`):

#### Start the Spanner Emulator via Docker:
```bash
# 1. Start the Spanner emulator container
docker run -d --name spanner-emulator -p 9010:9010 -p 9020:9020 gcr.io/cloud-spanner-emulator/emulator

# 2. Point client libraries to the local emulator
export SPANNER_EMULATOR_HOST="localhost:9010"

# 3. Run end-to-end schema migration test suite
uv run pytest tests/test_schema_migrations.py -s
```

*(Optional) Stop the emulator container when finished:*
```bash
docker stop spanner-emulator && docker rm spanner-emulator
```

> **Note:** If `SPANNER_EMULATOR_HOST` is unset, static DDL syntax and topological ordering checks will still run, while live emulator tests are skipped gracefully.

### 4. Run Linter and Formatting Checks
```bash
uv run ruff check packages/datacommons-db/datacommons_db/migrations/ tests/test_schema_migrations.py
uv run ruff format --check packages/datacommons-db/datacommons_db/migrations/ tests/test_schema_migrations.py
```
