-- Provider installation metadata for opt-in PEP 420 atom distributions.

ALTER TABLE public.atom_source_repositories
    ADD COLUMN IF NOT EXISTS distribution_name TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS distribution_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS install_requirement TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS wheel_url TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS wheel_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE public.atom_source_repositories
    DROP CONSTRAINT IF EXISTS atom_source_repositories_wheel_sha256_format;
ALTER TABLE public.atom_source_repositories
    ADD CONSTRAINT atom_source_repositories_wheel_sha256_format
    CHECK (wheel_sha256 = '' OR wheel_sha256 ~ '^[0-9a-f]{64}$');

DROP POLICY IF EXISTS atom_source_repositories_select_anon_active
    ON public.atom_source_repositories;
CREATE POLICY atom_source_repositories_select_anon_active
    ON public.atom_source_repositories
    FOR SELECT TO anon
    USING (active = TRUE);
GRANT SELECT ON public.atom_source_repositories TO anon, authenticated;

CREATE OR REPLACE VIEW public.catalog_atom_installations
WITH (security_invoker = true)
AS
SELECT
    served.atom_id,
    served.fqdn,
    CONCAT_WS(
        '.',
        NULLIF(atoms.namespace_root, ''),
        NULLIF(atoms.source_module_path, '')
    ) AS import_module,
    atoms.source_symbol AS import_symbol,
    repositories.repo_name AS provider_id,
    repositories.distribution_name,
    repositories.distribution_version,
    repositories.install_requirement,
    repositories.wheel_url,
    repositories.wheel_sha256
FROM public.catalog_atoms_served AS served
JOIN public.atoms AS atoms
  ON atoms.atom_id = served.atom_id
JOIN public.atom_source_repositories AS repositories
  ON repositories.source_repo_id = atoms.source_repo_id
WHERE repositories.active = TRUE
  AND repositories.distribution_name <> ''
  AND repositories.distribution_version <> ''
  AND repositories.install_requirement <> '';

GRANT SELECT ON public.catalog_atom_installations TO anon, authenticated;
