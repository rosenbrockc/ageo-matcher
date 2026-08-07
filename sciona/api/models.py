"""Pydantic request/response models for the platform API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class DeviceFlowResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str = ""
    expires_in: int


class PendingResponse(BaseModel):
    status: str = "authorization_pending"
    interval: int = 5


class UserResponse(BaseModel):
    user_id: UUID
    github_login: str
    display_name: str
    avatar_url: str
    identity_tier: str
    effective_tier: str = "general"
    reputation_score: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Atoms / Registry
# ---------------------------------------------------------------------------


class AtomPublishRequest(BaseModel):
    fqdn: str
    semver: str
    description: str = ""
    domain_tags: list[str] = Field(default_factory=list)
    source_tar_b64: str = Field(
        ..., description="Base64-encoded tar.gz of the atom source."
    )
    fingerprint: str = Field(
        ..., description="Full SHA-256 AST fingerprint (64 hex chars)."
    )
    authors: list[AuthorShare] | None = None


class AuthorShare(BaseModel):
    github_login: str
    contribution_share: float = 1.0


class AtomPublishResponse(BaseModel):
    atom_id: UUID
    version_id: UUID
    fqdn: str
    content_hash: str
    semver: str
    is_new_atom: bool


class AtomDetailResponse(BaseModel):
    atom_id: UUID
    fqdn: str
    description: str
    domain_tags: list[str]
    status: str
    owner_github_login: str
    latest_version: AtomVersionResponse | None = None
    created_at: datetime


class AtomVersionResponse(BaseModel):
    version_id: UUID
    content_hash: str
    semver: str
    is_latest: bool
    fingerprint: str
    created_at: datetime


class AtomSummaryResponse(BaseModel):
    atom_id: UUID
    fqdn: str
    description: str
    domain_tags: list[str]
    status: str
    latest_semver: str = ""


# ---------------------------------------------------------------------------
# Bounties
# ---------------------------------------------------------------------------


class BountyCreateRequest(BaseModel):
    title: str
    escrow_amount: float = Field(..., gt=0)
    deadline: datetime | None = None
    tier: str = "standard"
    domain_tags: list[str] = Field(default_factory=list)
    flare_payload: dict | None = None
    config_yml: dict = Field(default_factory=dict)


class BountyResponse(BaseModel):
    bounty_id: UUID
    principal_id: UUID
    title: str
    escrow_amount: float
    status: str
    deadline: datetime | None
    tier: str
    verification_budget: int
    verifications_used: int
    submission_count: int = 0
    created_at: datetime
    updated_at: datetime


class BountyFundResponse(BaseModel):
    bounty_id: UUID
    status: str
    checkout_url: str = ""


class BountyCancelResponse(BaseModel):
    bounty_id: UUID
    status: str
    cancellation_fee: float


class BountySummaryResponse(BaseModel):
    bounty_id: UUID
    title: str
    escrow_amount: float
    status: str
    deadline: datetime | None
    tier: str
    submission_count: int = 0
    domain_tags: list[str] = Field(default_factory=list)


class SubmissionRequest(BaseModel):
    cdg_hash: str
    atom_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of atom FQDN to content hash.",
    )
    receipt_json: dict
    claimed_metric_name: str
    claimed_metric_value: float


class SubmissionResponse(BaseModel):
    submission_id: UUID
    bounty_id: UUID
    verification_status: str
    submitted_at: datetime


class UpdateTargetRequest(BaseModel):
    min_metric_value: float


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class ProviderInstallInfo(BaseModel):
    provider_id: str
    distribution_name: str
    distribution_version: str
    install_requirement: str
    import_module: str
    import_symbol: str
    wheel_url: str = ""
    wheel_sha256: str = ""


class CatalogEntry(BaseModel):
    fqdn: str
    description: str
    artifact_kind: str = "atom"
    domain_tags: list[str] = Field(default_factory=list)
    latest_semver: str = ""
    status: str = "approved"
    overall_verdict: str = ""
    risk_tier: str = ""
    trust_readiness: str = ""
    provider: ProviderInstallInfo | None = None
    score: float = 0.0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""

    items: list = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
