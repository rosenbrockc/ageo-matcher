-- Allow dataset compatibility to target canonical provider atoms as well as
-- unified CDG/state artifacts. FQDN remains the stable lookup identity.

ALTER TABLE public.artifact_data_compatibility
    ADD COLUMN IF NOT EXISTS consumer_fqdn TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS consumer_atom_id UUID REFERENCES public.atoms(atom_id)
        ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS consumer_atom_version_id UUID
        REFERENCES public.atom_versions(version_id) ON DELETE CASCADE;

UPDATE public.artifact_data_compatibility compatibility
SET consumer_fqdn = artifact.fqdn
FROM public.artifacts artifact
WHERE artifact.artifact_id = compatibility.consumer_artifact_id
  AND compatibility.consumer_fqdn = '';

ALTER TABLE public.artifact_data_compatibility
    ALTER COLUMN consumer_artifact_id DROP NOT NULL;
ALTER TABLE public.artifact_data_compatibility
    DROP CONSTRAINT IF EXISTS artifact_data_compatibility_consumer_identity_check;
ALTER TABLE public.artifact_data_compatibility
    ADD CONSTRAINT artifact_data_compatibility_consumer_identity_check
    CHECK (
        consumer_fqdn <> ''
        AND num_nonnulls(consumer_artifact_id, consumer_atom_id) = 1
        AND (consumer_version_id IS NULL OR consumer_artifact_id IS NOT NULL)
        AND (consumer_atom_version_id IS NULL OR consumer_atom_id IS NOT NULL)
    );

DROP INDEX IF EXISTS public.idx_artifact_data_compatibility_identity;
CREATE UNIQUE INDEX idx_artifact_data_compatibility_identity
    ON public.artifact_data_compatibility (
        consumer_fqdn,
        COALESCE(consumer_version_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(consumer_atom_version_id, '00000000-0000-0000-0000-000000000000'::uuid),
        input_port,
        data_version_id,
        compatibility_kind
    );
CREATE INDEX IF NOT EXISTS idx_artifact_data_compatibility_consumer_fqdn
    ON public.artifact_data_compatibility (consumer_fqdn, input_port);

CREATE OR REPLACE FUNCTION public.catalog_data_artifacts(
    p_consumer_fqdn TEXT DEFAULT NULL,
    p_input_port TEXT DEFAULT NULL
)
RETURNS TABLE (
    fqn TEXT,
    version_id UUID,
    semver TEXT,
    content_hash TEXT,
    name TEXT,
    description TEXT,
    modality TEXT,
    media_type TEXT,
    schema_json JSONB,
    shape BIGINT[],
    dtype TEXT,
    sampling_metadata JSONB,
    attribution JSONB,
    license_expression TEXT,
    source_uri TEXT,
    intended_use TEXT,
    limitations TEXT[],
    assets JSONB,
    compatibility_kind TEXT,
    input_port TEXT,
    evidence_json JSONB,
    confidence DOUBLE PRECISION
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT
        data_artifact.fqdn,
        data_version.version_id,
        data_version.semver,
        data_version.content_hash,
        COALESCE(NULLIF(metadata.schema_json ->> 'name', ''), data_artifact.fqdn),
        data_artifact.description,
        metadata.modality,
        metadata.media_type,
        metadata.schema_json,
        metadata.shape,
        metadata.dtype,
        metadata.sampling_metadata,
        metadata.attribution,
        metadata.license_expression,
        metadata.source_uri,
        metadata.intended_use,
        metadata.limitations,
        COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'asset_path', asset.asset_path,
                        'byte_size', asset.byte_size,
                        'sha256', asset.sha256,
                        'format', asset.format,
                        'media_type', asset.media_type,
                        'storage_uri', asset.storage_uri,
                        'compression', asset.compression,
                        'mmap_safe', asset.mmap_safe,
                        'loader_name', asset.loader_name
                    )
                    ORDER BY asset.asset_path
                )
                FROM public.artifact_assets asset
                WHERE asset.version_id = data_version.version_id
            ),
            '[]'::jsonb
        ),
        compatibility.compatibility_kind,
        compatibility.input_port,
        compatibility.evidence_json,
        compatibility.confidence
    FROM public.artifacts data_artifact
    JOIN public.artifact_versions data_version
      ON data_version.artifact_id = data_artifact.artifact_id
     AND data_version.is_latest = TRUE
    JOIN public.data_artifact_metadata metadata
      ON metadata.version_id = data_version.version_id
    LEFT JOIN LATERAL (
        SELECT compatibility_row.*
        FROM public.artifact_data_compatibility compatibility_row
        WHERE compatibility_row.data_version_id = data_version.version_id
          AND compatibility_row.compatibility_kind <> 'incompatible'
          AND compatibility_row.consumer_fqdn = p_consumer_fqdn
          AND (p_input_port IS NULL OR compatibility_row.input_port IN ('', p_input_port))
        ORDER BY
            CASE compatibility_row.compatibility_kind
                WHEN 'validated' THEN 0
                WHEN 'benchmark' THEN 1
                ELSE 2
            END,
            compatibility_row.confidence DESC,
            compatibility_row.created_at DESC
        LIMIT 1
    ) compatibility ON p_consumer_fqdn IS NOT NULL
    WHERE data_artifact.artifact_kind = 'data_artifact'
      AND data_artifact.status = 'approved'
      AND data_artifact.visibility_tier = 'general'
      AND data_artifact.is_publishable = TRUE
      AND (
          p_consumer_fqdn IS NULL
          OR compatibility.compatibility_id IS NOT NULL
      )
    ORDER BY
        compatibility.confidence DESC NULLS LAST,
        data_artifact.fqdn;
$$;

GRANT EXECUTE ON FUNCTION public.catalog_data_artifacts(TEXT, TEXT)
    TO anon, authenticated;
