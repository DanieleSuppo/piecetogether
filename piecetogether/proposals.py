"""Typed, versioned model output and deterministic semantic validation."""

from dataclasses import dataclass, field
from typing import Any, Literal

from .contracts import DomainContract, valid_name, value_matches


@dataclass(frozen=True)
class EntityOperation:
    id: str
    entity_type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    action: Literal["create", "resolve"] = "create"
    kind: Literal["entity"] = field(default="entity", init=False)


@dataclass(frozen=True)
class ContextOperation:
    id: str
    entity_ids: tuple[str, ...] = ()
    action: Literal["create", "resolve"] = "create"
    kind: Literal["context"] = field(default="context", init=False)


@dataclass(frozen=True)
class ClaimOperation:
    id: str
    target_id: str
    concept: str
    value: Any
    grounding_policy: str
    source_communication_id: str | None = None
    kind: Literal["claim"] = field(default="claim", init=False)


@dataclass(frozen=True)
class RelationshipOperation:
    relationship_type: str
    source_id: str
    target_id: str
    kind: Literal["relationship"] = field(default="relationship", init=False)


@dataclass(frozen=True)
class GroundingPlanOperation:
    policy: str
    claim_ids: tuple[str, ...]
    acceptance_mode: Literal["explicit", "implicit"]
    kind: Literal["grounding_plan"] = field(default="grounding_plan", init=False)


@dataclass(frozen=True)
class GroundingResolutionOperation:
    item_id: str
    policy: str
    evidence_communication_id: str
    acceptance_mode: Literal["explicit", "implicit"]
    outcome: Literal["accepted", "rejected", "corrected"]
    kind: Literal["grounding_resolution"] = field(default="grounding_resolution", init=False)


@dataclass(frozen=True)
class ArtifactOperation:
    id: str
    artifact_type: str
    roles: tuple[str, ...]
    action: Literal["classify", "persist", "supersede", "delete"] = "classify"
    retention: dict[str, str] | None = None
    predecessor_id: str | None = None
    content_reference: str | None = None
    source_communication_id: str | None = None
    kind: Literal["artifact"] = field(default="artifact", init=False)


@dataclass(frozen=True)
class EmergentConceptOperation:
    name: str
    description: str
    status: Literal["non_authoritative"] = "non_authoritative"
    kind: Literal["emergent_concept"] = field(default="emergent_concept", init=False)


@dataclass(frozen=True)
class ContextRequestOperation:
    scope_ids: tuple[str, ...]
    purpose: Literal["continuity", "disambiguation", "grounding"]
    budget: int
    kind: Literal["context_request"] = field(default="context_request", init=False)


Operation = (
    EntityOperation | ContextOperation | ClaimOperation | RelationshipOperation
    | GroundingPlanOperation | GroundingResolutionOperation
    | ArtifactOperation
    | EmergentConceptOperation
    | ContextRequestOperation
)


@dataclass(frozen=True)
class CandidateClaim:
    source_communication_id: str
    interpretation: str
    status: str = "candidate"


@dataclass(frozen=True)
class SemanticProposal:
    schema_version: int
    contract_version: str
    communication_id: str
    candidate_claims: tuple[CandidateClaim, ...]
    draft_response: str
    operations: tuple[Operation, ...] = ()
    intent: Literal["candidate", "semantic_commit"] = "candidate"


@dataclass(frozen=True)
class ValidationResult:
    outcome: Literal["accepted", "rejected", "stale"]
    reasons: tuple[str, ...] = ()


def validate(
    proposal: SemanticProposal, communication_id: str, contract: DomainContract
) -> ValidationResult:
    # Malformed provider output is an operational parsing failure.
    if (
        not isinstance(proposal, SemanticProposal)
        or not valid_name(proposal.contract_version)
        or not valid_name(proposal.communication_id)
        or not valid_name(proposal.intent)
        or not isinstance(proposal.draft_response, str)
        or not proposal.draft_response.strip()
        or len(proposal.draft_response) > 65536
        or not isinstance(proposal.candidate_claims, tuple)
        or any(
            not isinstance(claim, CandidateClaim)
            or not isinstance(claim.interpretation, str)
            or not claim.interpretation.strip()
            or len(claim.interpretation) > 32768
            for claim in proposal.candidate_claims
        )
    ):
        raise ValueError("invalid proposal envelope")
    if len(proposal.candidate_claims) > 256:
        return ValidationResult("rejected", ("too_many_candidate_claims",))
    if type(proposal.schema_version) is not int or proposal.schema_version != 1:
        return ValidationResult("rejected", ("unsupported_proposal_version",))
    if proposal.communication_id != communication_id or any(
        claim.source_communication_id != communication_id or claim.status != "candidate"
        for claim in proposal.candidate_claims
    ):
        return ValidationResult("rejected", ("invalid_provenance_or_candidate_status",))
    if proposal.contract_version != contract.version:
        return ValidationResult("stale", ("contract_version_changed",))
    if proposal.intent != "candidate":
        return ValidationResult("rejected", ("trusted_commit_unavailable",))
    if not isinstance(proposal.operations, tuple) or len(proposal.operations) > 256:
        return ValidationResult("rejected", ("invalid_operations",))
    targets: dict[str, str] = {}
    emergent: set[str] = set()
    for operation in proposal.operations:
        if type(operation) not in (
            EntityOperation, ContextOperation, ClaimOperation, RelationshipOperation,
            GroundingPlanOperation, GroundingResolutionOperation,
            ArtifactOperation,
            EmergentConceptOperation,
            ContextRequestOperation,
        ):
            return ValidationResult("rejected", ("unsupported_operation",))
        if isinstance(operation, EntityOperation) and not valid_name(operation.entity_type):
            return ValidationResult("rejected", ("entity_type_not_allowed",))
        if isinstance(operation, EmergentConceptOperation):
            if not contract.emergent_concepts["allowed"]:
                return ValidationResult("rejected", ("emergent_concepts_not_allowed",))
            if not valid_name(operation.name) or operation.name in contract.claim_concepts:
                return ValidationResult("rejected", ("emergent_canonical_collision",))
            if operation.status != "non_authoritative":
                return ValidationResult("rejected", ("emergent_authority_not_allowed",))
            if operation.name in emergent:
                return ValidationResult("rejected", ("duplicate_emergent_concept",))
            if not isinstance(operation.description, str) or not operation.description.strip() or len(operation.description) > 32768:
                return ValidationResult("rejected", ("invalid_emergent_description",))
            emergent.add(operation.name)
        if isinstance(operation, (EntityOperation, ContextOperation, ClaimOperation, ArtifactOperation)):
            if not valid_name(operation.id) or operation.id in targets:
                return ValidationResult("rejected", ("invalid_or_duplicate_id",))
            targets[operation.id] = (
                operation.entity_type if isinstance(operation, EntityOperation)
                else "$context" if isinstance(operation, ContextOperation)
                else "$claim" if isinstance(operation, ClaimOperation) else "$artifact"
            )
    for operation in proposal.operations:
        reason = operation_reason(operation, targets, communication_id, contract, emergent)
        if reason:
            return ValidationResult("rejected", (reason,))
        if isinstance(operation, GroundingPlanOperation) and any(
            isinstance(claim, ClaimOperation) and claim.id in operation.claim_ids
            and claim.grounding_policy != operation.policy
            for claim in proposal.operations
        ):
            return ValidationResult("rejected", ("grounding_policy_not_allowed",))
    return ValidationResult("accepted")


def operation_reason(
    operation: Operation, targets: dict[str, str], communication_id: str,
    contract: DomainContract, emergent: set[str],
) -> str | None:
    if isinstance(operation, EntityOperation):
        if not valid_name(operation.entity_type) or operation.entity_type not in contract.entity_types:
            return "entity_type_not_allowed"
        policy = contract.entity_types[operation.entity_type]
        if operation.action == "resolve":
            return "entity_resolution_unavailable"
        if operation.action != "create" or not policy["creation"]:
            return "entity_creation_not_allowed"
        if not value_matches(
            operation.attributes, {"type": "object", "properties": policy["attributes"]}
        ):
            return "invalid_entity_attributes"
    elif isinstance(operation, ContextOperation):
        if operation.action != "create":
            return "context_resolution_unavailable"
        if not isinstance(operation.entity_ids, tuple) or any(
            not valid_name(entity_id) or targets.get(entity_id) not in contract.entity_types
            for entity_id in operation.entity_ids
        ):
            return "invalid_context_entities"
    elif isinstance(operation, ClaimOperation):
        if operation.source_communication_id not in (None, communication_id):
            return "invalid_claim_provenance"
        if not valid_name(operation.concept) or (
            operation.concept not in contract.claim_concepts
            and operation.concept not in emergent
        ):
            return "claim_concept_not_allowed"
        policy = contract.claim_concepts.get(operation.concept, contract.emergent_concepts)
        if not valid_name(operation.target_id) or targets.get(operation.target_id) not in policy["target_types"]:
            return "claim_target_not_allowed"
        if not valid_name(operation.grounding_policy) or operation.grounding_policy != policy["grounding_policy"]:
            return "grounding_policy_not_allowed"
        if not value_matches(operation.value, policy["value"]):
            return "claim_value_not_allowed"
    elif isinstance(operation, RelationshipOperation):
        if not valid_name(operation.relationship_type) or operation.relationship_type not in contract.relationship_types:
            return "relationship_type_not_allowed"
        policy = contract.relationship_types[operation.relationship_type]
        if (
            not valid_name(operation.source_id) or not valid_name(operation.target_id)
            or targets.get(operation.source_id) not in policy["source_types"]
            or targets.get(operation.target_id) not in policy["target_types"]
        ):
            return "relationship_endpoints_not_allowed"
    elif isinstance(operation, (GroundingPlanOperation, GroundingResolutionOperation)):
        if not valid_name(operation.policy) or operation.policy not in contract.grounding_policies:
            return "grounding_policy_not_allowed"
        if operation.acceptance_mode not in contract.grounding_policies[operation.policy]["acceptance"]:
            return "grounding_mode_not_allowed"
        if isinstance(operation, GroundingPlanOperation):
            if not isinstance(operation.claim_ids, tuple) or not operation.claim_ids or any(
                not valid_name(claim_id) or targets.get(claim_id) != "$claim"
                for claim_id in operation.claim_ids
            ):
                return "grounding_target_not_allowed"
            if len(set(operation.claim_ids)) != len(operation.claim_ids):
                return "duplicate_grounding_target"
        else:
            # No persisted/exposed items exist until #17. Model assertions are not evidence.
            return "grounding_item_unavailable"
    elif isinstance(operation, ArtifactOperation):
        if operation.source_communication_id not in (None, communication_id):
            return "invalid_artifact_provenance"
        if not valid_name(operation.artifact_type) or operation.artifact_type not in contract.artifact_types:
            return "artifact_type_not_allowed"
        policy = contract.artifact_types[operation.artifact_type]
        if not isinstance(operation.roles, tuple) or not operation.roles or any(
            not valid_name(role) or role not in policy["roles"] for role in operation.roles
        ):
            return "artifact_roles_not_allowed"
        if policy["persistence"] == "required" and "persistent-domain-artifact" not in operation.roles:
            return "artifact_persistence_required"
        if operation.retention is not None and operation.retention != policy["retention"]:
            return "artifact_retention_not_allowed"
        if operation.action == "persist":
            if policy["persistence"] == "forbidden":
                return "artifact_persistence_not_allowed"
            # Only ArtifactStore-owned staged bytes can support persistence (#19).
            return "artifact_staging_unavailable"
        if operation.action == "supersede":
            return "artifact_predecessor_unavailable" if policy["supersession"] else "artifact_supersession_not_allowed"
        if operation.action == "delete":
            return "artifact_lifecycle_unavailable"
        if operation.action != "classify" or operation.content_reference is not None or operation.predecessor_id is not None:
            return "invalid_artifact_classification"
    elif isinstance(operation, ContextRequestOperation):
        if (
            not isinstance(operation.scope_ids, tuple) or not operation.scope_ids
            or len(operation.scope_ids) > 256
            or any(not valid_name(scope_id) for scope_id in operation.scope_ids)
            or operation.purpose not in ("continuity", "disambiguation", "grounding")
            or type(operation.budget) is not int or operation.budget <= 0
        ):
            return "invalid_context_request"
        # #16 owns scope, disclosure eligibility and bounded retrieval execution.
        return "context_retrieval_unavailable"
    return None
