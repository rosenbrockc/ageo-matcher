# Agent Instructions for sciona-matcher

## Python Environment

Use the virtual environment at `/Users/conrad/personal/sciona-matcher/.venv` for all Python operations (tests, installs, imports).

```bash
/Users/conrad/personal/sciona-matcher/.venv/bin/python -m pytest ...
```

All sibling provider repos (`sciona-atoms`, `sciona-atoms-signal`, `sciona-atoms-ml`, etc.) are installed into this venv. Do not use the system Python or conda environments.

## Templated Dataset Confidentiality

Treat templated datasets and their metadata as non-public. Never commit dataset
contents, names, recording or subject identifiers, source paths, filenames,
directory layouts, schemas, channel inventories, excerpts, timestamps,
checksums, URLs, or generated adapters derived from a real template.

Use synthetic committed fixtures. Real-data evaluations must receive their
inputs through local runtime configuration excluded from Git, and committed
benchmark evidence may contain only opaque aliases and aggregate metrics.
Inspect staged evaluation and generated files for identifying metadata before
every commit. If publication appears necessary, stop and request an explicitly
approved public representation.
