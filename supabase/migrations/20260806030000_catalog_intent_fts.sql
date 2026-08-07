-- Improve natural-language fallback recall without discipline-specific aliases.

CREATE OR REPLACE FUNCTION public.catalog_relaxed_tsquery(query_text TEXT)
RETURNS TSQUERY
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    WITH lexemes AS (
        SELECT tsvector_to_array(
            to_tsvector('english', COALESCE(query_text, ''))
        ) AS values
    )
    SELECT CASE
        WHEN COALESCE(array_length(values, 1), 0) = 0 THEN NULL
        ELSE to_tsquery('english', array_to_string(values, ' | '))
    END
    FROM lexemes;
$$;

CREATE OR REPLACE FUNCTION public.search_atoms_fts(
    query_text TEXT,
    result_limit INTEGER DEFAULT 20,
    result_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    atom_id UUID,
    fqdn TEXT,
    technical_description TEXT,
    dejargonized_description TEXT,
    domain_tags TEXT[],
    overall_verdict TEXT,
    risk_tier TEXT,
    trust_readiness TEXT,
    fts_rank REAL
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH query_input AS (
        SELECT
            websearch_to_tsquery(
                'english', NULLIF(BTRIM(query_text), '')
            ) AS strict_tsq,
            public.catalog_relaxed_tsquery(query_text) AS relaxed_tsq
    ),
    candidates AS (
        SELECT
            ci.*,
            public.catalog_search_document(
                ci.fqdn,
                ci.technical_description,
                ci.dejargonized_description,
                ci.domain_tags
            ) AS document,
            query_input.strict_tsq,
            query_input.relaxed_tsq
        FROM public.catalog_atoms_served ci
        CROSS JOIN query_input
    )
    SELECT
        candidates.atom_id,
        candidates.fqdn,
        candidates.technical_description,
        candidates.dejargonized_description,
        candidates.domain_tags,
        candidates.overall_verdict,
        candidates.risk_tier,
        candidates.trust_readiness,
        (
            ts_rank_cd(candidates.document, candidates.relaxed_tsq)
            + CASE
                WHEN candidates.strict_tsq IS NOT NULL
                 AND candidates.document @@ candidates.strict_tsq
                THEN 1.0
                ELSE 0.0
              END
        )::REAL AS fts_rank
    FROM candidates
    WHERE candidates.relaxed_tsq IS NOT NULL
      AND candidates.document @@ candidates.relaxed_tsq
    ORDER BY fts_rank DESC, candidates.fqdn
    LIMIT result_limit
    OFFSET result_offset;
$$;

GRANT EXECUTE ON FUNCTION public.catalog_relaxed_tsquery(TEXT)
    TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_atoms_fts(TEXT, INTEGER, INTEGER)
    TO anon, authenticated;
