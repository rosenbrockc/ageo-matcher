"""Deterministic, domain-neutral query normalization for no-key catalog search."""

from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[a-z0-9_]+")

_CONCEPT_GROUPS = (
    frozenset({"minimum", "least", "lowest", "cheapest", "shortest", "optimal"}),
    frozenset({"item", "vertex", "node", "endpoint"}),
    frozenset(
        {
            "route",
            "routes",
            "path",
            "paths",
            "connection",
            "connections",
            "link",
            "links",
            "reachable",
        }
    ),
    frozenset({"convert", "translate", "transform"}),
    frozenset({"position", "state", "vector", "estimate"}),
    frozenset({"advance", "forecast", "predict", "project", "propagate"}),
    frozenset({"rate", "cadence", "frequency"}),
    frozenset({"event", "peak", "pulse", "beat"}),
    frozenset({"validate", "verify", "check", "guard"}),
)

_EXPANSIONS = {
    token: group
    for group in _CONCEPT_GROUPS
    for token in group
}


def expand_catalog_query_tokens(query: str) -> set[str]:
    """Return lexical tokens plus conservative operational synonyms."""
    tokens = set(TOKEN_RE.findall(query.lower()))
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_EXPANSIONS.get(token, ()))
    earth_frame = (
        "earth" in tokens
        and bool({"centered", "fixed"} & tokens)
        and bool(
            {"axes", "cartesian", "coordinates", "frame", "x", "y", "z"}
            & tokens
        )
    )
    if "ecef" in tokens or earth_frame:
        expanded.update(
            {"altitude", "ecef", "latitude", "lla", "longitude", "wgs84"}
        )
    return expanded
