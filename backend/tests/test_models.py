"""Tests for SQLAlchemy ORM models and the initial Alembic migration.

Validates model definitions (table names, columns, types, FKs, indexes,
relationships) and that the migration file is structurally correct —
all without requiring a live database.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, Integer, String, Text, Boolean, Float, DateTime
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.models.db import (
    AlertRule,
    ApprovalRequest,
    Base,
    ClassifierOutput,
    Incident,
    IncidentEvent,
    Playbook,
    PlaybookVersion,
    Project,
    Tool,
    TriggerRule,
    User,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# All models importable and have correct table names
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "users": User,
    "tools": Tool,
    "projects": Project,
    "playbooks": Playbook,
    "playbook_versions": PlaybookVersion,
    "trigger_rules": TriggerRule,
    "incidents": Incident,
    "incident_events": IncidentEvent,
    "classifier_outputs": ClassifierOutput,
    "approval_requests": ApprovalRequest,
    "alert_rules": AlertRule,
}


class TestModelRegistry:
    def test_base_has_all_tables(self):
        table_names = set(Base.metadata.tables.keys())
        for name in EXPECTED_TABLES:
            assert name in table_names, f"Table '{name}' not in metadata"

    def test_exactly_11_tables(self):
        # 10 tables from backend_architecture.md + 1 projects (Phase 9.2, DECISION-017)
        assert len(Base.metadata.tables) == 11

    @pytest.mark.parametrize("table_name,model_cls", EXPECTED_TABLES.items())
    def test_tablename_matches(self, table_name, model_cls):
        assert model_cls.__tablename__ == table_name


# ---------------------------------------------------------------------------
# Column existence per model
# ---------------------------------------------------------------------------

TOOL_COLUMNS = [
    "id", "name", "display_name", "description", "category",
    "input_schema", "output_schema", "side_effect_level",
    "required_approvals", "environments_allowed", "embedding_text",
    "embedding", "is_active", "created_at", "updated_at",
]

PLAYBOOK_COLUMNS = [
    "id", "name", "description", "owner_team", "created_by",
    "created_at", "updated_at",
]

PLAYBOOK_VERSION_COLUMNS = [
    "id", "playbook_id", "version_number", "status",
    "trigger_bindings", "workflow_spec", "spec_checksum",
    "approval_info", "compatibility_metadata", "embedding_text",
    "embedding", "change_notes", "created_by", "created_at", "updated_at",
]

TRIGGER_RULE_COLUMNS = [
    "id", "incident_type", "entity_filter", "playbook_version_id",
    "priority", "is_active", "created_at", "updated_at",
]

INCIDENT_COLUMNS = [
    "id", "incident_key", "incident_type", "status", "severity",
    "entity", "sources", "evidence", "playbook_version_id",
    "temporal_workflow_id", "temporal_run_id", "resolved_at",
    "created_at", "updated_at",
]

INCIDENT_EVENT_COLUMNS = [
    "id", "incident_id", "event_type", "payload", "actor", "created_at",
]

CLASSIFIER_OUTPUT_COLUMNS = [
    "id", "entity_key", "window_start", "window_end", "label",
    "confidence", "evidence", "severity_suggestion", "incident_id",
    "created_at",
]

APPROVAL_REQUEST_COLUMNS = [
    "id", "incident_id", "workflow_id", "step_name",
    "requested_action", "status", "requested_by", "decided_by",
    "decision_notes", "expires_at", "created_at", "decided_at",
]

ALERT_RULE_COLUMNS = [
    "id", "name", "description", "rule_type", "rule_definition",
    "severity", "is_active", "created_by", "created_at", "updated_at",
]

USER_COLUMNS = [
    "id", "email", "display_name", "role", "hashed_password",
    "is_active", "created_at",
]


class TestToolColumns:
    @pytest.mark.parametrize("col", TOOL_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["tools"]
        assert col in table.columns, f"Missing column: tools.{col}"


class TestPlaybookColumns:
    @pytest.mark.parametrize("col", PLAYBOOK_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["playbooks"]
        assert col in table.columns, f"Missing column: playbooks.{col}"


class TestPlaybookVersionColumns:
    @pytest.mark.parametrize("col", PLAYBOOK_VERSION_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["playbook_versions"]
        assert col in table.columns, f"Missing column: playbook_versions.{col}"


class TestTriggerRuleColumns:
    @pytest.mark.parametrize("col", TRIGGER_RULE_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["trigger_rules"]
        assert col in table.columns, f"Missing column: trigger_rules.{col}"


class TestIncidentColumns:
    @pytest.mark.parametrize("col", INCIDENT_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["incidents"]
        assert col in table.columns, f"Missing column: incidents.{col}"


class TestIncidentEventColumns:
    @pytest.mark.parametrize("col", INCIDENT_EVENT_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["incident_events"]
        assert col in table.columns, f"Missing column: incident_events.{col}"


class TestClassifierOutputColumns:
    @pytest.mark.parametrize("col", CLASSIFIER_OUTPUT_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["classifier_outputs"]
        assert col in table.columns, f"Missing column: classifier_outputs.{col}"


class TestApprovalRequestColumns:
    @pytest.mark.parametrize("col", APPROVAL_REQUEST_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["approval_requests"]
        assert col in table.columns, f"Missing column: approval_requests.{col}"


class TestAlertRuleColumns:
    @pytest.mark.parametrize("col", ALERT_RULE_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["alert_rules"]
        assert col in table.columns, f"Missing column: alert_rules.{col}"


class TestUserColumns:
    @pytest.mark.parametrize("col", USER_COLUMNS)
    def test_column_exists(self, col):
        table = Base.metadata.tables["users"]
        assert col in table.columns, f"Missing column: users.{col}"


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------

EXPECTED_FKS = [
    ("playbook_versions", "playbook_id", "playbooks.id"),
    ("trigger_rules", "playbook_version_id", "playbook_versions.id"),
    ("incidents", "playbook_version_id", "playbook_versions.id"),
    ("incident_events", "incident_id", "incidents.id"),
    ("classifier_outputs", "incident_id", "incidents.id"),
    ("approval_requests", "incident_id", "incidents.id"),
]


class TestForeignKeys:
    @pytest.mark.parametrize("table_name,col_name,target", EXPECTED_FKS)
    def test_fk_exists(self, table_name, col_name, target):
        table = Base.metadata.tables[table_name]
        col = table.columns[col_name]
        fk_targets = [str(fk.target_fullname) for fk in col.foreign_keys]
        assert target in fk_targets, (
            f"{table_name}.{col_name} should FK to {target}, got {fk_targets}"
        )


# ---------------------------------------------------------------------------
# Cascade deletes
# ---------------------------------------------------------------------------


class TestCascadeDeletes:
    def test_playbook_versions_cascade(self):
        table = Base.metadata.tables["playbook_versions"]
        col = table.columns["playbook_id"]
        for fk in col.foreign_keys:
            assert fk.ondelete == "CASCADE"

    def test_incident_events_cascade(self):
        table = Base.metadata.tables["incident_events"]
        col = table.columns["incident_id"]
        for fk in col.foreign_keys:
            assert fk.ondelete == "CASCADE"


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------


class TestUniqueConstraints:
    def test_tools_name_unique(self):
        table = Base.metadata.tables["tools"]
        assert table.columns["name"].unique

    def test_users_email_unique(self):
        table = Base.metadata.tables["users"]
        assert table.columns["email"].unique

    def test_incidents_incident_key_unique(self):
        table = Base.metadata.tables["incidents"]
        assert table.columns["incident_key"].unique

    def test_playbook_version_composite_unique(self):
        table = Base.metadata.tables["playbook_versions"]
        unique_constraints = [
            c for c in table.constraints
            if hasattr(c, "columns") and len(c.columns) == 2
        ]
        col_sets = [
            set(c.name for c in uc.columns) for uc in unique_constraints
        ]
        assert {"playbook_id", "version_number"} in col_sets


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

EXPECTED_INDEXES = [
    ("tools", "idx_tools_name"),
    ("tools", "idx_tools_category"),
    ("playbook_versions", "idx_playbook_versions_playbook_id"),
    ("playbook_versions", "idx_playbook_versions_status"),
    ("trigger_rules", "idx_trigger_rules_incident_type"),
    ("trigger_rules", "idx_trigger_rules_active"),
    ("incidents", "idx_incidents_incident_key"),
    ("incidents", "idx_incidents_status"),
    ("incidents", "idx_incidents_type"),
    ("incidents", "idx_incidents_created_at"),
    ("incident_events", "idx_incident_events_incident_id"),
    ("incident_events", "idx_incident_events_created_at"),
    ("classifier_outputs", "idx_classifier_outputs_entity_key"),
    ("classifier_outputs", "idx_classifier_outputs_created_at"),
    ("approval_requests", "idx_approval_requests_status"),
    ("approval_requests", "idx_approval_requests_incident_id"),
]


class TestIndexes:
    @pytest.mark.parametrize("table_name,index_name", EXPECTED_INDEXES)
    def test_index_defined(self, table_name, index_name):
        table = Base.metadata.tables[table_name]
        index_names = [idx.name for idx in table.indexes]
        assert index_name in index_names, (
            f"Missing index {index_name} on {table_name}. Found: {index_names}"
        )


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


class TestRelationships:
    def test_playbook_has_versions_relationship(self):
        mapper = Playbook.__mapper__
        assert "versions" in mapper.relationships

    def test_playbook_version_has_playbook_relationship(self):
        mapper = PlaybookVersion.__mapper__
        assert "playbook" in mapper.relationships

    def test_incident_has_events_relationship(self):
        mapper = Incident.__mapper__
        assert "events" in mapper.relationships

    def test_incident_event_has_incident_relationship(self):
        mapper = IncidentEvent.__mapper__
        assert "incident" in mapper.relationships

    def test_trigger_rule_has_playbook_version_relationship(self):
        mapper = TriggerRule.__mapper__
        assert "playbook_version" in mapper.relationships


# ---------------------------------------------------------------------------
# pgvector columns
# ---------------------------------------------------------------------------


class TestVectorColumns:
    def test_tools_has_vector_column(self):
        table = Base.metadata.tables["tools"]
        col = table.columns["embedding"]
        assert col.nullable is True

    def test_playbook_versions_has_vector_column(self):
        table = Base.metadata.tables["playbook_versions"]
        col = table.columns["embedding"]
        assert col.nullable is True


# ---------------------------------------------------------------------------
# ARRAY columns
# ---------------------------------------------------------------------------


class TestArrayColumns:
    def test_tools_environments_allowed(self):
        table = Base.metadata.tables["tools"]
        col = table.columns["environments_allowed"]
        assert isinstance(col.type, ARRAY)

    def test_incidents_sources(self):
        table = Base.metadata.tables["incidents"]
        col = table.columns["sources"]
        assert isinstance(col.type, ARRAY)


# ---------------------------------------------------------------------------
# JSONB columns
# ---------------------------------------------------------------------------

JSONB_COLUMNS = [
    ("tools", "input_schema"),
    ("tools", "output_schema"),
    ("playbook_versions", "workflow_spec"),
    ("playbook_versions", "trigger_bindings"),
    ("playbook_versions", "approval_info"),
    ("playbook_versions", "compatibility_metadata"),
    ("trigger_rules", "entity_filter"),
    ("incidents", "entity"),
    ("incidents", "evidence"),
    ("incident_events", "payload"),
    ("classifier_outputs", "evidence"),
    ("alert_rules", "rule_definition"),
]


class TestJsonbColumns:
    @pytest.mark.parametrize("table_name,col_name", JSONB_COLUMNS)
    def test_jsonb_type(self, table_name, col_name):
        table = Base.metadata.tables[table_name]
        col = table.columns[col_name]
        assert isinstance(col.type, JSONB), (
            f"{table_name}.{col_name} should be JSONB, got {type(col.type)}"
        )


# ---------------------------------------------------------------------------
# Migration file structural tests
# ---------------------------------------------------------------------------

MIGRATION_DIR = BACKEND_DIR / "alembic" / "versions"


class TestMigrationFile:
    def test_migration_file_exists(self):
        files = list(MIGRATION_DIR.glob("001_*.py"))
        assert len(files) == 1, f"Expected 1 migration file, found {len(files)}"

    def test_migration_importable(self):
        import importlib.util
        migration_file = list(MIGRATION_DIR.glob("001_*.py"))[0]
        spec = importlib.util.spec_from_file_location("migration_001", migration_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "upgrade")
        assert hasattr(mod, "downgrade")
        assert mod.revision == "001"
        assert mod.down_revision is None

    def test_migration_has_extension_creation(self):
        migration_file = list(MIGRATION_DIR.glob("001_*.py"))[0]
        content = migration_file.read_text()
        assert "CREATE EXTENSION IF NOT EXISTS vector" in content
        assert "uuid-ossp" in content

    def test_migration_creates_all_tables(self):
        # Tables are spread across multiple migration files; concatenate all.
        content = "\n".join(f.read_text() for f in MIGRATION_DIR.glob("*.py"))
        for table_name in EXPECTED_TABLES:
            assert f'"{table_name}"' in content, (
                f"Migration should create table '{table_name}'"
            )

    def test_downgrade_drops_all_tables(self):
        content = "\n".join(f.read_text() for f in MIGRATION_DIR.glob("*.py"))
        for table_name in EXPECTED_TABLES:
            assert f'drop_table("{table_name}")' in content, (
                f"Downgrade should drop table '{table_name}'"
            )


# ---------------------------------------------------------------------------
# alembic/env.py wired to Base.metadata
# ---------------------------------------------------------------------------


class TestAlembicEnvWiring:
    def test_env_py_imports_base(self):
        env_file = BACKEND_DIR / "alembic" / "env.py"
        content = env_file.read_text()
        assert "from app.models.db import Base" in content

    def test_env_py_sets_target_metadata(self):
        env_file = BACKEND_DIR / "alembic" / "env.py"
        content = env_file.read_text()
        assert "target_metadata = Base.metadata" in content
