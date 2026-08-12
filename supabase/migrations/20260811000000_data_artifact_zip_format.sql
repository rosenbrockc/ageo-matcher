-- Permit checksum-pinned ZIP archives whose loader selects a declared member.

ALTER TABLE public.artifact_assets
    DROP CONSTRAINT IF EXISTS artifact_assets_format_check;

ALTER TABLE public.artifact_assets
    ADD CONSTRAINT artifact_assets_format_check
    CHECK (format IN (
        'safetensors', 'onnx', 'json', 'jsonl', 'parquet',
        'npy', 'npz', 'txt', 'vocab', 'zip'
    ));
