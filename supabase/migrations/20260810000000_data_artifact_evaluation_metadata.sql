-- Store reference-evaluation contracts with data artifacts so scoring can evolve
-- independently from atom packages and visualizer code.

ALTER TABLE public.data_artifact_metadata
    ADD COLUMN IF NOT EXISTS evaluation_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_data_artifact_metadata_evaluation
    ON public.data_artifact_metadata USING gin (evaluation_metadata);

DROP FUNCTION IF EXISTS public.catalog_data_artifacts(TEXT, TEXT);

CREATE FUNCTION public.catalog_data_artifacts(
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
    evaluation JSONB,
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
        metadata.evaluation_metadata,
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
      AND (p_consumer_fqdn IS NULL OR compatibility.compatibility_id IS NOT NULL)
    ORDER BY compatibility.confidence DESC NULLS LAST, data_artifact.fqdn;
$$;

GRANT EXECUTE ON FUNCTION public.catalog_data_artifacts(TEXT, TEXT)
    TO anon, authenticated;
