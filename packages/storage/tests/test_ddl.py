from dataclasses import replace
from pathlib import Path

from bioadapt_storage import APP_SCHEMA, Col, MIGRATION_NAME, TableSpec, lint, migration_drift, render_postgres
from bioadapt_storage.ddl import SCOPE_GUC
from bioadapt_storage.schema import is_secret_column_name

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def table(name: str) -> TableSpec:
    return next(t for t in APP_SCHEMA if t.name == name)


class TestLint:
    def test_the_shipped_schema_passes_every_rule(self):
        assert lint() == []

    def test_key_material_columns_are_named_but_token_counts_are_fine(self):
        assert is_secret_column_name("access_token")
        assert is_secret_column_name("provider_secret")
        assert not is_secret_column_name("input_tokens")
        assert not is_secret_column_name("content_fingerprint")

    def test_a_public_verifier_is_the_one_exception_and_the_whole_of_it(self):
        """§11.2 keeps secret material out of the index; a login key's public half is not secret.

        The exception matches on the exact name, so nothing shaped like a secret slips through beside it.
        """
        assert not is_secret_column_name("public_key")
        assert is_secret_column_name("private_key")
        assert is_secret_column_name("public_key_secret")

    def test_the_identity_directory_holds_a_verifier_and_no_secret(self):
        directory = table("iam_credential")
        assert directory.column("public_key") is not None
        assert directory.project_scoped
        assert not any(is_secret_column_name(col.name) for col in directory.cols)

    def test_a_secret_column_is_a_violation(self):
        audit = table("audit_log")
        bad = (table("project"), replace(audit, cols=audit.cols + (Col("api_key", "text"),)))
        assert any("key material" in v for v in lint(bad))

    def test_unscoped_tables_are_a_violation(self):
        bad = (
            table("project"),
            TableSpec(name="evidence_card", cols=(Col("card_id", "text", primary_key=True),), project_scoped=False),
        )
        assert any("must be project scoped" in v for v in lint(bad))

    def test_scoped_tables_need_a_project_id_column(self):
        bad = (table("project"), TableSpec(name="orphan", cols=(Col("orphan_id", "text", primary_key=True),)))
        assert any("need a project_id column" in v for v in lint(bad))

    def test_tables_need_a_primary_key(self):
        bad = (table("project"), TableSpec(name="nokey", cols=(Col("project_id", "text", nullable=False),)))
        assert any("no primary key" in v for v in lint(bad))

    def test_append_only_tables_must_record_time(self):
        bad = (
            table("project"),
            TableSpec(
                name="trail",
                cols=(Col("project_id", "text", nullable=False), Col("trail_id", "text", primary_key=True)),
                append_only=True,
            ),
        )
        assert any("append-only tables must record when" in v for v in lint(bad))

    def test_a_schema_without_a_project_table_is_rejected(self):
        assert lint(tuple(t for t in APP_SCHEMA if t.name != "project")) == [
            "the schema must declare the project table"
        ]


class TestPostgresRender:
    def setup_method(self):
        self.sql = render_postgres(APP_SCHEMA)

    def test_every_table_is_row_level_secured_on_the_project_scope(self):
        for spec in APP_SCHEMA:
            assert f'alter table "{spec.name}" enable row level security;' in self.sql
            assert f'create policy "{spec.name}_project_scope" on "{spec.name}"' in self.sql
        assert f"current_setting('{SCOPE_GUC}', true)" in self.sql

    def test_evidence_tables_get_append_only_triggers(self):
        for name in ("asset_lineage", "model_invocation", "review_event", "egress_decision", "audit_log"):
            assert f'create trigger "{name}_append_only" before update or delete on "{name}"' in self.sql
        assert "bioadapt_forbid_mutation()" in self.sql

    def test_the_effective_annotation_version_index_is_partial_and_unique(self):
        assert (
            'create unique index if not exists "annotation_version_status_key" on "annotation_version" '
            '("run_id", "label_column") where "status" = \'active\';' in self.sql
        )

    def test_committed_migration_matches_the_schema_spec(self):
        assert migration_drift(MIGRATIONS) is False
        assert (MIGRATIONS / MIGRATION_NAME).read_text(encoding="utf-8") == self.sql
