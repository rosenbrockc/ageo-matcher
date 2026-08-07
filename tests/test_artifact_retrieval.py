from __future__ import annotations

import pytest

from sciona.services.artifact_retrieval import MacroArtifactRetriever
from sciona.services.models import MacroArtifactCandidate, MacroMatchRequest


@pytest.mark.asyncio
async def test_macro_artifact_retriever_is_order_stable() -> None:
    request = MacroMatchRequest(goal="detect heart rate from ecg signal")
    better = MacroArtifactCandidate(
        fqdn="pkg.signal.rate_detector",
        semver="1.2.0",
        content_hash="bbb",
        name="Heart Rate Detector",
        description="Detect heart rate from ECG signal",
        conceptual_summary="rate detector for ecg signal",
        verified_leaf_coverage=0.9,
    )
    worse = MacroArtifactCandidate(
        fqdn="pkg.signal.filter",
        semver="1.0.0",
        content_hash="aaa",
        name="Signal Filter",
        description="Filter noisy ECG waveform",
        conceptual_summary="signal cleanup helper",
        verified_leaf_coverage=0.2,
    )

    first = MacroArtifactRetriever([worse, better], min_score=0.3)
    second = MacroArtifactRetriever([better, worse], min_score=0.3)

    first_result = await first.match_goal(request)
    second_result = await second.match_goal(request)

    assert first_result.success is True
    assert second_result.success is True
    assert first_result.candidate is not None
    assert second_result.candidate is not None
    assert first_result.candidate.fqdn == better.fqdn
    assert second_result.candidate.fqdn == better.fqdn


@pytest.mark.asyncio
async def test_macro_artifact_retriever_rejects_weak_candidates() -> None:
    retriever = MacroArtifactRetriever(
        [
            MacroArtifactCandidate(
                fqdn="pkg.math.integrate",
                semver="0.1.0",
                content_hash="aaa",
                name="Integrator",
                description="Integrate scalar time series",
            )
        ],
        min_score=0.8,
    )

    result = await retriever.match_goal(MacroMatchRequest(goal="detect heart rate"))

    assert result.success is False
    assert result.rejection_reason == "macro_score_below_threshold"


@pytest.mark.asyncio
async def test_discipline_specific_candidate_requires_query_context() -> None:
    generic = MacroArtifactCandidate(
        fqdn="cdg.skeleton.signal_detect_measure",
        name="Generic Signal Event Rate",
        description="Condition a sampled waveform, detect events, and estimate a rate.",
        domain_tags=["signal", "event_rate"],
        score=0.8,
    )
    ecg = MacroArtifactCandidate(
        fqdn="cdg.signal.ecg_heart_rate",
        name="ECG Heart Rate Pipeline",
        description="Detect heart rate from an ECG waveform.",
        domain_tags=["signal", "event_rate", "ecg"],
        required_context_tags=["ecg"],
        score=1.0,
    )
    retriever = MacroArtifactRetriever([ecg, generic], min_score=0.1)

    labeled = await retriever.match_goal(
        MacroMatchRequest(goal="detect heart rate from raw ECG")
    )
    masked = await retriever.match_goal(
        MacroMatchRequest(goal="estimate event rate from a sampled waveform")
    )

    assert labeled.candidate is not None
    assert labeled.candidate.fqdn == ecg.fqdn
    assert masked.candidate is not None
    assert masked.candidate.fqdn == generic.fqdn
