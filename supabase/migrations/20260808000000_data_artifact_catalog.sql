-- First-class data artifacts and evidence-backed atom/CDG compatibility.

ALTER TABLE public.artifacts
    DROP CONSTRAINT IF EXISTS artifacts_artifact_kind_check;
ALTER TABLE public.artifacts
    ADD CONSTRAINT artifacts_artifact_kind_check
    CHECK (artifact_kind IN ('atom', 'cdg', 'state_artifact', 'data_artifact'));

CREATE TABLE IF NOT EXISTS public.data_artifact_metadata (
    version_id UUID PRIMARY KEY REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    modality TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    schema_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    shape BIGINT[] NOT NULL DEFAULT '{}',
    dtype TEXT NOT NULL DEFAULT '',
    sampling_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    attribution JSONB NOT NULL DEFAULT '{}'::jsonb,
    license_expression TEXT NOT NULL DEFAULT '',
    source_uri TEXT NOT NULL DEFAULT '',
    intended_use TEXT NOT NULL DEFAULT '',
    limitations TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_data_artifact_metadata_modality
    ON public.data_artifact_metadata (modality)
    WHERE modality <> '';
CREATE INDEX IF NOT EXISTS idx_data_artifact_metadata_schema
    ON public.data_artifact_metadata USING gin (schema_json);

CREATE TABLE IF NOT EXISTS public.artifact_data_compatibility (
    compatibility_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consumer_artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    consumer_version_id UUID REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    input_port TEXT NOT NULL DEFAULT '',
    data_version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    compatibility_kind TEXT NOT NULL
        CHECK (compatibility_kind IN (
            'example', 'validated', 'benchmark', 'incompatible'
        )),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_data_compatibility_identity
    ON public.artifact_data_compatibility (
        consumer_artifact_id,
        COALESCE(consumer_version_id, '00000000-0000-0000-0000-000000000000'::uuid),
        input_port,
        data_version_id,
        compatibility_kind
    );
CREATE INDEX IF NOT EXISTS idx_artifact_data_compatibility_consumer
    ON public.artifact_data_compatibility (consumer_artifact_id, input_port);
CREATE INDEX IF NOT EXISTS idx_artifact_data_compatibility_data
    ON public.artifact_data_compatibility (data_version_id);

ALTER TABLE public.data_artifact_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.artifact_data_compatibility ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS data_artifact_metadata_select_public
    ON public.data_artifact_metadata;
CREATE POLICY data_artifact_metadata_select_public
    ON public.data_artifact_metadata
    FOR SELECT TO anon, authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.artifact_versions av
            JOIN public.artifacts a ON a.artifact_id = av.artifact_id
            WHERE av.version_id = data_artifact_metadata.version_id
              AND a.artifact_kind = 'data_artifact'
              AND a.status = 'approved'
              AND a.visibility_tier = 'general'
              AND a.is_publishable = TRUE
        )
    );

DROP POLICY IF EXISTS artifact_data_compatibility_select_public
    ON public.artifact_data_compatibility;
CREATE POLICY artifact_data_compatibility_select_public
    ON public.artifact_data_compatibility
    FOR SELECT TO anon, authenticated
    USING (
        compatibility_kind <> 'incompatible'
        AND EXISTS (
            SELECT 1
            FROM public.artifact_versions av
            JOIN public.artifacts a ON a.artifact_id = av.artifact_id
            WHERE av.version_id = artifact_data_compatibility.data_version_id
              AND a.artifact_kind = 'data_artifact'
              AND a.status = 'approved'
              AND a.visibility_tier = 'general'
              AND a.is_publishable = TRUE
        )
    );

GRANT SELECT ON public.data_artifact_metadata TO anon, authenticated;
GRANT SELECT ON public.artifact_data_compatibility TO anon, authenticated;

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
        JOIN public.artifacts consumer
          ON consumer.artifact_id = compatibility_row.consumer_artifact_id
        WHERE compatibility_row.data_version_id = data_version.version_id
          AND compatibility_row.compatibility_kind <> 'incompatible'
          AND consumer.fqdn = p_consumer_fqdn
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
