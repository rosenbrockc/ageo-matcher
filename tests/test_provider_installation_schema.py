from pathlib import Path


def test_provider_installation_migration_exposes_pinned_distribution_contract() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260806000000_provider_installation_catalog.sql"
    ).read_text()

    assert "ADD COLUMN IF NOT EXISTS distribution_name" in migration
    assert "ADD COLUMN IF NOT EXISTS distribution_version" in migration
    assert "ADD COLUMN IF NOT EXISTS install_requirement" in migration
    assert "CREATE OR REPLACE VIEW public.catalog_atom_installations" in migration
    assert "public.catalog_atoms_served" in migration
    assert "repositories.active = TRUE" in migration


def test_provider_import_module_migration_uses_explicit_python_target() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260806010000_atom_provider_import_modules.sql"
    ).read_text()

    assert "ADD COLUMN IF NOT EXISTS import_module" in migration
    assert "NULLIF(atoms.import_module, '')" in migration
    assert "CREATE OR REPLACE VIEW public.catalog_atom_installations" in migration
