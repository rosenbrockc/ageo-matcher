-- Production embedding provenance and active-space coordination.

ALTER TABLE public.atom_embeddings
    ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'openai',
    ADD COLUMN IF NOT EXISTS model_revision TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    ADD COLUMN IF NOT EXISTS response_model TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS input_schema_version TEXT NOT NULL DEFAULT 'atom-search-v1',
    ADD COLUMN IF NOT EXISTS embedding_space_id TEXT NOT NULL DEFAULT '';

UPDATE public.atom_embeddings
SET embedding_space_id = concat_ws(
    ':',
    provider,
    model,
    model_revision,
    dimensions::TEXT,
    input_schema_version
)
WHERE embedding_space_id = '';

ALTER TABLE public.atom_embeddings
    ADD CONSTRAINT atom_embeddings_provider_nonempty
        CHECK (btrim(provider) <> ''),
    ADD CONSTRAINT atom_embeddings_model_revision_nonempty
        CHECK (btrim(model_revision) <> ''),
    ADD CONSTRAINT atom_embeddings_input_schema_nonempty
        CHECK (btrim(input_schema_version) <> ''),
    ADD CONSTRAINT atom_embeddings_space_nonempty
        CHECK (btrim(embedding_space_id) <> '');

CREATE INDEX IF NOT EXISTS idx_atom_embeddings_space
    ON public.atom_embeddings (embedding_space_id);

CREATE TABLE IF NOT EXISTS public.catalog_embedding_configuration (
    configuration_id BOOLEAN PRIMARY KEY DEFAULT TRUE
        CHECK (configuration_id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions = 1536),
    input_schema_version TEXT NOT NULL,
    embedding_space_id TEXT NOT NULL UNIQUE,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.catalog_embedding_configuration (
    configuration_id,
    provider,
    model,
    model_revision,
    dimensions,
    input_schema_version,
    embedding_space_id
)
SELECT
    TRUE,
    ae.provider,
    ae.model,
    ae.model_revision,
    ae.dimensions,
    ae.input_schema_version,
    ae.embedding_space_id
FROM public.atom_embeddings ae
ORDER BY ae.updated_at DESC
LIMIT 1
ON CONFLICT (configuration_id) DO NOTHING;

ALTER TABLE public.catalog_embedding_configuration ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.catalog_embedding_configuration FROM PUBLIC;
GRANT SELECT ON TABLE public.catalog_embedding_configuration TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.catalog_embedding_configuration TO service_role;

DROP POLICY IF EXISTS catalog_embedding_configuration_select
    ON public.catalog_embedding_configuration;
CREATE POLICY catalog_embedding_configuration_select
    ON public.catalog_embedding_configuration
    FOR SELECT
    USING (TRUE);

-- Search functions are security-invoker functions. RLS therefore ensures they
-- never compare a query vector with rows from a different embedding space.
DROP POLICY IF EXISTS atom_embeddings_select_visible ON public.atom_embeddings;
CREATE POLICY atom_embeddings_select_visible ON public.atom_embeddings
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_embeddings.atom_id
        )
        AND embedding_space_id = (
            SELECT configuration.embedding_space_id
            FROM public.catalog_embedding_configuration configuration
            WHERE configuration.configuration_id
        )
    );

DROP FUNCTION IF EXISTS public.get_atoms_needing_embeddings();
CREATE FUNCTION public.get_atoms_needing_embeddings(
    expected_provider TEXT,
    expected_model TEXT,
    expected_model_revision TEXT,
    expected_dimensions INTEGER,
    expected_input_schema_version TEXT,
    expected_embedding_space_id TEXT
)
RETURNS TABLE (
    atom_id UUID,
    fqdn TEXT,
    technical_description TEXT,
    dejargonized_description TEXT,
    domain_tags TEXT[]
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT
        ci.atom_id,
        ci.fqdn,
        ci.technical_description,
        ci.dejargonized_description,
        ci.domain_tags
    FROM public.catalog_atoms_served ci
    LEFT JOIN public.atom_embeddings ae
      ON ae.atom_id = ci.atom_id
    WHERE ae.atom_id IS NULL
       OR ae.input_text_hash IS DISTINCT FROM public.atom_embedding_input_hash(
            ci.fqdn,
            ci.technical_description,
            ci.dejargonized_description,
            ci.domain_tags
        )
       OR ae.provider IS DISTINCT FROM expected_provider
       OR ae.model IS DISTINCT FROM expected_model
       OR ae.model_revision IS DISTINCT FROM expected_model_revision
       OR ae.dimensions IS DISTINCT FROM expected_dimensions
       OR ae.input_schema_version IS DISTINCT FROM expected_input_schema_version
       OR ae.embedding_space_id IS DISTINCT FROM expected_embedding_space_id
    ORDER BY ci.fqdn;
$$;

REVOKE EXECUTE ON FUNCTION public.get_atoms_needing_embeddings(
    TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_atoms_needing_embeddings(
    TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT
) TO service_role;

CREATE OR REPLACE FUNCTION public.get_active_embedding_configuration()
RETURNS TABLE (
    provider TEXT,
    model TEXT,
    model_revision TEXT,
    dimensions INTEGER,
    input_schema_version TEXT,
    embedding_space_id TEXT,
    activated_at TIMESTAMPTZ
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT
        configuration.provider,
        configuration.model,
        configuration.model_revision,
        configuration.dimensions,
        configuration.input_schema_version,
        configuration.embedding_space_id,
        configuration.activated_at
    FROM public.catalog_embedding_configuration configuration
    WHERE configuration.configuration_id;
$$;

GRANT EXECUTE ON FUNCTION public.get_active_embedding_configuration()
    TO anon, authenticated;
