-- Explicit Python import targets for provider-backed atoms.

ALTER TABLE public.atoms
    ADD COLUMN IF NOT EXISTS import_module TEXT NOT NULL DEFAULT '';

CREATE OR REPLACE VIEW public.catalog_atom_installations
WITH (security_invoker = true)
AS
SELECT
    served.atom_id,
    served.fqdn,
    COALESCE(
        NULLIF(atoms.import_module, ''),
        CONCAT_WS(
            '.',
            NULLIF(atoms.namespace_root, ''),
            NULLIF(atoms.source_module_path, '')
        )
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
