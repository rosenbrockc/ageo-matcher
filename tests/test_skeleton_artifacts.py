from __future__ import annotations

import pytest

from sciona.services.skeleton_artifacts import (
    build_local_skeleton_macro_retriever,
    load_local_skeleton_macro_candidates,
)
from sciona.services.models import MacroMatchRequest


def test_local_skeleton_macro_candidates_are_deterministic() -> None:
    candidates = load_local_skeleton_macro_candidates()

    assert candidates
    assert [candidate.fqdn for candidate in candidates] == sorted(
        candidate.fqdn for candidate in candidates
    )
    assert {
        candidate.fqdn for candidate in candidates
    } >= {
        "cdg.sciona_atoms_signal.ecg_heart_rate_biosppy",
        "cdg.skeleton.belief_propagation",
        "cdg.skeleton.kalman_filter",
        "cdg.skeleton.particle_filter",
        "cdg.skeleton.signal_detect_measure",
    }
    signal_candidate = next(
        candidate
        for candidate in candidates
        if candidate.fqdn == "cdg.skeleton.signal_detect_measure"
    )
    assert signal_candidate.terminal_on_match is False
    assert signal_candidate.artifact_kind == "cdg"
    assert "event_rate_estimation" in signal_candidate.domain_tags
    assert signal_candidate.cdg is not None
    assert signal_candidate.cdg.metadata["artifact_fqdn"] == signal_candidate.fqdn
    assert signal_candidate.cdg.metadata["artifact_source"] == "local_skeleton_asset"


@pytest.mark.asyncio
async def test_local_skeleton_macro_retriever_matches_belief_propagation() -> None:
    retriever = build_local_skeleton_macro_retriever(min_score=0.3)

    result = await retriever.match_goal(
        MacroMatchRequest(goal="Run belief propagation over a factor graph")
    )

    assert result.success is True
    assert result.candidate is not None
    assert result.candidate.fqdn == "cdg.skeleton.belief_propagation"
    assert result.candidate.terminal_on_match is False


@pytest.mark.asyncio
async def test_local_skeleton_macro_retriever_prefers_concrete_ecg_cdg() -> None:
    retriever = build_local_skeleton_macro_retriever(min_score=0.3)

    result = await retriever.match_goal(
        MacroMatchRequest(goal="Detect heart rate from ECG signal")
    )

    assert result.success is True
    assert result.candidate is not None
    assert result.candidate.fqdn == "cdg.sciona_atoms_signal.ecg_heart_rate_biosppy"
    assert result.candidate.terminal_on_match is False


@pytest.mark.asyncio
async def test_local_skeleton_macro_retriever_uses_generic_signal_cdg_when_masked() -> None:
    retriever = build_local_skeleton_macro_retriever(min_score=0.3)

    result = await retriever.match_goal(
        MacroMatchRequest(goal="Estimate event rate from an unlabeled sampled waveform")
    )

    assert result.success is True
    assert result.candidate is not None
    assert result.candidate.fqdn == "cdg.skeleton.signal_detect_measure"


@pytest.mark.asyncio
async def test_local_skeleton_macro_retriever_matches_kalman_filter() -> None:
    retriever = build_local_skeleton_macro_retriever(min_score=0.3)

    result = await retriever.match_goal(
        MacroMatchRequest(goal="Estimate hidden state with a Kalman filter")
    )

    assert result.success is True
    assert result.candidate is not None
    assert result.candidate.fqdn == "cdg.skeleton.kalman_filter"
    assert result.candidate.terminal_on_match is False


@pytest.mark.asyncio
async def test_local_skeleton_macro_retriever_matches_particle_filter() -> None:
    retriever = build_local_skeleton_macro_retriever(min_score=0.3)

    result = await retriever.match_goal(
        MacroMatchRequest(goal="Track latent state with a particle filter")
    )

    assert result.success is True
    assert result.candidate is not None
    assert result.candidate.fqdn == "cdg.skeleton.particle_filter"
    assert result.candidate.terminal_on_match is False
