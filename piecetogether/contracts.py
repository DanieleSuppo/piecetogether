"""Deployment-scoped declarative Contracts; no executable domain rules."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


def valid_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def finite_number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def require_record(
    value: Any, allowed: set[str], required: set[str] | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or any(not valid_name(key) for key in value)
        or set(value) - allowed
        or not (required or set()) <= value.keys()
    ):
        raise ValueError("invalid declarative Contract record")
    return value


def unique_record(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError("duplicate Contract declaration key")
    return data


def validate_constraint(data: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("Contract constraint nesting exceeds limit")
    rule = require_record(data, {
        "type", "enum", "minimum", "maximum", "min_length", "max_length",
        "properties", "items",
    }, {"type"})
    kind = rule["type"]
    if kind not in ("string", "boolean", "integer", "number", "object", "array"):
        raise ValueError("unsupported Contract value type")
    for key in ("minimum", "maximum"):
        if key in rule and (
            kind not in ("integer", "number")
            or not finite_number(rule[key])
        ):
            raise ValueError("invalid numeric constraint")
    for key in ("min_length", "max_length"):
        if key in rule and (
            kind not in ("string", "array")
            or type(rule[key]) is not int
            or rule[key] < 0
        ):
            raise ValueError("invalid length constraint")
    for low, high in (("minimum", "maximum"), ("min_length", "max_length")):
        if low in rule and high in rule and rule[low] > rule[high]:
            raise ValueError("contradictory Contract constraints")
    if "properties" in rule:
        if kind != "object" or not isinstance(rule["properties"], dict):
            raise ValueError("invalid object constraint")
        for key, nested in rule["properties"].items():
            if not valid_name(key):
                raise ValueError("invalid property name")
            validate_constraint(nested, depth + 1)
    if "items" in rule:
        if kind != "array":
            raise ValueError("items requires an array")
        validate_constraint(rule["items"], depth + 1)
    if kind == "array" and "items" not in rule:
        raise ValueError("array items must be constrained")
    if "enum" in rule:
        if not isinstance(rule["enum"], list) or not rule["enum"] or any(
            not value_matches(value, {
                key: setting for key, setting in rule.items() if key != "enum"
            })
            for value in rule["enum"]
        ):
            raise ValueError("invalid Contract enumeration")


def value_matches(value: Any, rule: dict[str, Any]) -> bool:
    kind = rule["type"]
    if kind == "number":
        valid = finite_number(value)
    else:
        valid = type(value) is {
            "string": str, "boolean": bool, "integer": int,
            "object": dict, "array": list,
        }[kind]
    if not valid:
        return False
    if "enum" in rule and not any(type(value) is type(item) and value == item for item in rule["enum"]):
        return False
    if "minimum" in rule and value < rule["minimum"]:
        return False
    if "maximum" in rule and value > rule["maximum"]:
        return False
    if "min_length" in rule and len(value) < rule["min_length"]:
        return False
    if "max_length" in rule and len(value) > rule["max_length"]:
        return False
    if kind == "object":
        properties = rule.get("properties", {})
        return all(
            key in properties and value_matches(item, properties[key])
            for key, item in value.items()
        )
    if kind == "array":
        return all(value_matches(item, rule["items"]) for item in value)
    return True


@dataclass(frozen=True)
class DomainContract:
    version: str
    entity_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationship_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    claim_concepts: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifact_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    grounding_policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    emergent_concepts: dict[str, Any] = field(default_factory=lambda: {"allowed": False})
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or not valid_name(self.version)
        ):
            raise ValueError("invalid Contract version")
        for catalogue in (
            self.entity_types, self.relationship_types, self.claim_concepts,
            self.artifact_types, self.grounding_policies,
        ):
            if not isinstance(catalogue, dict) or any(not valid_name(key) for key in catalogue):
                raise ValueError("invalid Contract catalogue")
        for policy in self.entity_types.values():
            rule = require_record(policy, {"creation", "attributes"}, {"creation", "attributes"})
            if type(rule["creation"]) is not bool:
                raise ValueError("creation must be boolean")
            validate_constraint({"type": "object", "properties": rule["attributes"]})
        if {"$context", "$claim", "$artifact"} & self.entity_types.keys():
            raise ValueError("reserved Entity Type name")
        for policy in self.grounding_policies.values():
            rule = require_record(policy, {"acceptance", "confirmation_forms"}, {"acceptance"})
            modes = rule["acceptance"]
            if not isinstance(modes, list) or not modes or any(
                mode not in ("explicit", "implicit") for mode in modes
            ):
                raise ValueError("invalid Grounding acceptance policy")
            if "confirmation_forms" in rule and (
                "explicit" not in modes or not isinstance(rule["confirmation_forms"], list)
                or not rule["confirmation_forms"] or any(
                    not isinstance(form, str) or not form.strip() or len(form) > 32768
                    for form in rule["confirmation_forms"]
                )
            ):
                raise ValueError("invalid explicit confirmation forms")
        for policy in self.relationship_types.values():
            rule = require_record(
                policy, {"source_types", "target_types"}, {"source_types", "target_types"}
            )
            self.check_targets(rule["source_types"], claims=True)
            self.check_targets(rule["target_types"], claims=True)
        for policy in self.claim_concepts.values():
            fields = {"target_types", "value", "grounding_policy"}
            rule = require_record(policy, fields, fields)
            self.check_targets(rule["target_types"])
            validate_constraint(rule["value"])
            if (
                not valid_name(rule["grounding_policy"])
                or rule["grounding_policy"] not in self.grounding_policies
            ):
                raise ValueError("unknown Grounding policy")
        for policy in self.artifact_types.values():
            fields = {"roles", "persistence", "retention", "supersession"}
            rule = require_record(policy, fields, fields)
            roles = rule["roles"]
            if not isinstance(roles, list) or not roles or any(
                role not in ("ephemeral-evidence", "source-evidence", "persistent-domain-artifact")
                for role in roles
            ):
                raise ValueError("invalid Artifact roles")
            if rule["persistence"] not in ("forbidden", "allowed", "required") or type(rule["supersession"]) is not bool:
                raise ValueError("invalid Artifact lifecycle policy")
            if (
                rule["persistence"] == "forbidden" and "persistent-domain-artifact" in roles
                or rule["persistence"] == "required" and "persistent-domain-artifact" not in roles
            ):
                raise ValueError("contradictory Artifact persistence policy")
            fields = {"metadata", "bytes", "provenance"}
            retention = require_record(rule["retention"], fields, fields)
            if any(value not in ("retain", "delete") for value in retention.values()):
                raise ValueError("invalid Artifact retention policy")
            if rule["persistence"] == "required" and retention["bytes"] != "retain":
                raise ValueError("persistent Artifacts require retained bytes")
        emergence = require_record(
            self.emergent_concepts,
            {"allowed", "target_types", "value", "grounding_policy"}, {"allowed"},
        )
        if type(emergence["allowed"]) is not bool:
            raise ValueError("emergent allowance must be boolean")
        if emergence["allowed"]:
            if set(emergence) != {"allowed", "target_types", "value", "grounding_policy"}:
                raise ValueError("Emergent Concepts require deterministic constraints")
            self.check_targets(emergence["target_types"])
            validate_constraint(emergence["value"])
            if (
                not valid_name(emergence["grounding_policy"])
                or emergence["grounding_policy"] not in self.grounding_policies
            ):
                raise ValueError("unknown emergent Grounding policy")
        elif set(emergence) != {"allowed"}:
            raise ValueError("disabled emergence cannot declare additional policy")

    def check_targets(self, values: Any, claims: bool = False) -> None:
        allowed = set(self.entity_types) | {"$context"}
        if claims:
            allowed.add("$claim")
        if not isinstance(values, list) or not values or any(
            not valid_name(value) or value not in allowed for value in values
        ):
            raise ValueError("invalid Contract endpoint types")

    @classmethod
    def from_dict(cls, data: Any) -> "DomainContract":
        fields = {
            "schema_version", "version", "entity_types", "relationship_types",
            "claim_concepts", "artifact_types", "grounding_policies", "emergent_concepts",
        }
        return cls(**require_record(data, fields, fields))


class DomainContractProvider(Protocol):
    def get(self, version: str) -> DomainContract: ...


class DeclarativeContractProvider:
    """A static first-party file provider, selected at bootstrap."""

    def __init__(self, path: Path | None):
        self.path = path

    def get(self, version: str) -> DomainContract:
        contract = (
            DomainContract.from_dict(json.loads(self.path.read_text(), object_pairs_hook=unique_record))
            if self.path else DomainContract(version)
        )
        if contract.version != version:
            raise ValueError("configured Contract version does not match declaration")
        return contract
