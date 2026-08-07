from __future__ import annotations

import pytest

from sciona.usability import (
    CanonicalUsabilityReasonDefinition,
    UsabilityAssessment,
    UsabilityBlockingReasonCode,
    UsabilityProvenance,
    UsabilityProvenanceKind,
    UsabilityReason,
    UsabilityReasonKind,
    UsabilityScope,
    UsabilityScopeAssessment,
    UsabilityWarningReasonCode,
    canonical_usability_reason_definition,
    canonical_usability_reason_definitions,
    known_canonical_usability_reason_definitions,
    known_usability_blocking_reason_codes,
    known_usability_reason_codes,
    known_usability_warning_reason_codes,
)


def _provenance(kind: UsabilityProvenanceKind, source_id: str) -> UsabilityProvenance:
    return UsabilityProvenance(kind=kind, source_id=source_id, note="shared provenance")


def _reason(
    kind: UsabilityReasonKind,
    code: str,
    summary: str,
    source_id: str,
    provenance_kind: UsabilityProvenanceKind = UsabilityProvenanceKind.RUNTIME_ASSESSOR,
) -> UsabilityReason:
    return UsabilityReason(
        kind=kind,
        code=code,
        summary=summary,
        confidence=0.75,
        uncertainty_notes=["review needed"],
        provenance=[_provenance(provenance_kind, source_id)],
    )


def test_usability_assessment_supports_three_scopes_and_round_trips() -> None:
    assessment = UsabilityAssessment(
        assessment_id="synthetic_member_001",
        family="signal_detect_measure",
        task_intent="detect_rate_from_waveform",
        heuristic_signature=["interval_instability", "quality_instability"],
        required_contracts_checked=["required_input_present", "required_reference_present"],
        usable_for_guidance=True,
        usable_for_scoring=False,
        usable_for_final_benchmark=False,
        confidence=0.8,
        uncertainty_notes=["shared usability is still being broadened."],
        provenance=[_provenance(UsabilityProvenanceKind.MANUAL_REVIEW, "review-1")],
        guidance=UsabilityScopeAssessment(
            scope=UsabilityScope.GUIDANCE,
            usable=True,
            confidence=0.7,
            warning_reasons=[
                _reason(
                    UsabilityReasonKind.WARNING,
                    UsabilityWarningReasonCode.PARTIAL_COVERAGE.value,
                    "Only part of the candidate context is covered.",
                    "guidance-coverage",
                )
            ],
        ),
        scoring=UsabilityScopeAssessment(
            scope=UsabilityScope.SCORING,
            usable=False,
            confidence=0.85,
            blocking_reasons=[
                _reason(
                    UsabilityReasonKind.BLOCKING,
                    UsabilityBlockingReasonCode.REQUIRED_REFERENCE_MISSING.value,
                    "Scoring reference is missing for this member.",
                    "scoring-evidence",
                    provenance_kind=UsabilityProvenanceKind.MANUAL_REVIEW,
                )
            ],
            warning_reasons=[
                _reason(
                    UsabilityReasonKind.WARNING,
                    UsabilityWarningReasonCode.REVIEW_RECOMMENDED.value,
                    "Score confidence is still modest.",
                    "scoring-confidence",
                    provenance_kind=UsabilityProvenanceKind.MANUAL_REVIEW,
                )
            ],
        ),
        final_benchmark=UsabilityScopeAssessment(
            scope=UsabilityScope.FINAL_BENCHMARK,
            usable=False,
            confidence=0.9,
            blocking_reasons=[
                _reason(
                    UsabilityReasonKind.BLOCKING,
                    UsabilityBlockingReasonCode.BENCHMARK_CONTRACT_MISMATCH.value,
                    "Final benchmark is not yet ready.",
                    "benchmark-fit",
                    provenance_kind=UsabilityProvenanceKind.BENCHMARK_POLICY,
                )
            ],
            warning_reasons=[
                _reason(
                    UsabilityReasonKind.WARNING,
                    UsabilityWarningReasonCode.REVIEW_RECOMMENDED.value,
                    "Final benchmark still needs review.",
                    "benchmark-review",
                    provenance_kind=UsabilityProvenanceKind.MANUAL_REVIEW,
                )
            ],
        ),
    )

    restored = UsabilityAssessment.model_validate(assessment.model_dump(mode="json"))
    assert restored.guidance.scope == UsabilityScope.GUIDANCE
    assert restored.scoring.scope == UsabilityScope.SCORING
    assert restored.final_benchmark.scope == UsabilityScope.FINAL_BENCHMARK
    assert restored.usable_for_guidance is True
    assert restored.usable_for_scoring is False
    assert restored.final_benchmark.warning_reasons[0].code == "review_recommended"


def test_usability_reason_codes_are_canonical_and_dejargonized() -> None:
    assert (
        UsabilityBlockingReasonCode.REQUIRED_REFERENCE_MISSING.value
        in known_usability_blocking_reason_codes()
    )
    assert (
        UsabilityWarningReasonCode.QUALITY_INSTABILITY.value
        in known_usability_warning_reason_codes()
    )
    assert "required_input_missing" in known_usability_reason_codes()


def test_canonical_reason_definitions_are_governed_and_cross_family() -> None:
    definition = canonical_usability_reason_definition("required_input_missing")
    catalog = known_canonical_usability_reason_definitions()

    assert isinstance(definition, CanonicalUsabilityReasonDefinition)
    assert definition.kind == UsabilityReasonKind.BLOCKING
    assert definition.shared_meaning
    assert definition.governance_rationale
    assert len(catalog) == len(known_usability_reason_codes())
    assert canonical_usability_reason_definitions()[0].code == sorted(
        known_usability_reason_codes()
    )[0]


def test_usability_rejects_domain_jargon_in_shared_reason_codes() -> None:
    with pytest.raises(ValueError, match="de-jargonized"):
        UsabilityReason(
            kind=UsabilityReasonKind.BLOCKING,
            code="ecg_peak_missing",
            summary="Domain jargon should not leak into shared codes.",
            provenance=[
                _provenance(UsabilityProvenanceKind.RUNTIME_ASSESSOR, "reason-1")
            ],
        )


def test_usability_rejects_kind_code_mismatch() -> None:
    with pytest.raises(ValueError, match="warning code"):
        UsabilityReason(
            kind=UsabilityReasonKind.BLOCKING,
            code=UsabilityWarningReasonCode.REVIEW_RECOMMENDED.value,
            summary="Blocking reasons cannot reuse warning codes.",
            provenance=[
                _provenance(UsabilityProvenanceKind.RUNTIME_ASSESSOR, "reason-2")
            ],
        )


def test_usability_scope_assessment_rejects_wrong_reason_kind() -> None:
    with pytest.raises(ValueError, match="blocking_reasons"):
        UsabilityScopeAssessment(
            scope=UsabilityScope.GUIDANCE,
            usable=False,
            blocking_reasons=[
                UsabilityReason(
                    kind=UsabilityReasonKind.WARNING,
                    code=UsabilityWarningReasonCode.REVIEW_RECOMMENDED.value,
                    summary="Wrong kind on purpose.",
                    provenance=[
                        _provenance(
                            UsabilityProvenanceKind.MANUAL_REVIEW,
                            "review-2",
                        )
                    ],
                )
            ],
        )


def test_usability_assessment_rejects_scope_mismatch() -> None:
    with pytest.raises(ValueError, match="guidance must use scope guidance"):
        UsabilityAssessment(
            assessment_id="test_assessment",
            usable_for_guidance=False,
            usable_for_scoring=False,
            usable_for_final_benchmark=False,
            guidance=UsabilityScopeAssessment(scope=UsabilityScope.SCORING, usable=False),
            scoring=UsabilityScopeAssessment(scope=UsabilityScope.SCORING, usable=False),
            final_benchmark=UsabilityScopeAssessment(
                scope=UsabilityScope.FINAL_BENCHMARK,
                usable=False,
            ),
        )


def test_usability_reason_requires_uncertainty_notes_when_confidence_is_partial() -> None:
    with pytest.raises(ValueError, match="uncertainty notes"):
        UsabilityReason(
            kind=UsabilityReasonKind.WARNING,
            code=UsabilityWarningReasonCode.REVIEW_RECOMMENDED.value,
            summary="The artifact should be reviewed before higher-stakes use.",
            confidence=0.4,
            provenance=[
                _provenance(UsabilityProvenanceKind.MANUAL_REVIEW, "review-3")
            ],
        )


def test_usability_reason_requires_structured_provenance() -> None:
    with pytest.raises(ValueError, match="structured provenance"):
        UsabilityReason(
            kind=UsabilityReasonKind.BLOCKING,
            code=UsabilityBlockingReasonCode.COVERAGE_INSUFFICIENT.value,
            summary="Coverage is too limited to support the intended use.",
        )


def test_usability_reason_requires_compatible_provenance_kind() -> None:
    with pytest.raises(ValueError, match="must include provenance compatible"):
        UsabilityReason(
            kind=UsabilityReasonKind.WARNING,
            code=UsabilityWarningReasonCode.QUALITY_INSTABILITY.value,
            summary="Quality varies enough that one fixed interpretation may be brittle.",
            provenance=[
                _provenance(UsabilityProvenanceKind.MANUAL_REVIEW, "review-4")
            ],
            uncertainty_notes=["Manual review alone is not the right provenance here."],
            confidence=0.5,
        )


def test_usability_assessment_rejects_boolean_scope_disagreement() -> None:
    with pytest.raises(ValueError, match="usable_for_guidance must match guidance.usable"):
        UsabilityAssessment(
            assessment_id="test_assessment",
            usable_for_guidance=True,
            usable_for_scoring=False,
            usable_for_final_benchmark=False,
            guidance=UsabilityScopeAssessment(scope=UsabilityScope.GUIDANCE, usable=False),
            scoring=UsabilityScopeAssessment(scope=UsabilityScope.SCORING, usable=False),
            final_benchmark=UsabilityScopeAssessment(
                scope=UsabilityScope.FINAL_BENCHMARK,
                usable=False,
            ),
        )
