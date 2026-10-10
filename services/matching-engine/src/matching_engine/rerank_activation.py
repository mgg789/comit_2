"""Audited operator-only sort rollout; sealed quality and review gates stay unchanged."""

import argparse
import json
import os
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt

from matching_engine.models import Contract
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import (
    RerankArtifact,
    artifact_hash,
    validate_artifact,
)

REVIEWED_ARTIFACT_HASH = "c18f4bc1f2926b58643f68d985e5598f6bc28ac8b8d03bfaeeab00472dcbbcc9"
REVIEWED_RECEIPT_HASH = "5149d72428631204cfa6d6b96c90e45bca6c9774593e87bc882d29c9fbb821fd"
PACKAGED_ARTIFACT = "artifacts/r0-c18f4bc1.json"
PACKAGED_DECISION = "artifacts/r0-c18f4bc1-manual-decision.json"
_RECEIPT_VERSION = "manual-sort-only-activation-v1"
_TRUST = object()
_HASH = r"^[0-9a-f]{64}$"


class ManualAcknowledgements(Contract):
    """Explicitly acknowledge the failed/missing research evidence without replacing it."""

    failed_quality_gate: StrictBool
    test_only_training: StrictBool
    missing_independent_human_review: StrictBool
    sort_only_no_tail: StrictBool
    review_limitations: StrictBool


class RolloutEvidence(Contract):
    """Pinned existing assistant evidence; it never claims an independent human review."""

    artifact_hash: str = Field(pattern=_HASH)
    dataset_hash: str = Field(pattern=_HASH)
    training_report_hash: str = Field(pattern=_HASH)
    correction_receipt_hash: str = Field(pattern=_HASH)
    assistant_comparison_hash: str = Field(pattern=_HASH)
    assistant_review_hash: str = Field(pattern=_HASH)
    assistant_packet_hash: str = Field(pattern=_HASH)
    assistant_pairs: StrictInt = Field(ge=20)
    factual_errors: StrictInt = Field(ge=0)
    disagreements: StrictInt = Field(ge=0)
    independent_human_evaluation: Literal[False]
    user_pairs: Literal[0]


class ActivationReceipt(Contract):
    """Immutable operator intent bound to an exact artifact and truthful evidence snapshots."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["manual-sort-only-activation-v1"]
    action: Literal["activate", "deactivate"]
    artifact_hash: str = Field(pattern=_HASH)
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    scope: Literal["sort_only"]
    gate_snapshot: dict
    reviews_snapshot: dict
    training_test_only: StrictBool
    evaluation_hash: str
    acknowledgements: ManualAcknowledgements | None
    review_evidence: RolloutEvidence | None
    receipt_hash: str = Field(pattern=_HASH)


class DbManualApproval:
    """Opaque runtime authority minted only after a persisted DB receipt is validated.

    JSON dictionaries, metadata inside an artifact, and CLI intent alone cannot
    be passed to pure scoring as an approval. The database is the trust boundary.
    """

    __slots__ = ("artifact_hash", "receipt_hash")

    def __init__(self, artifact_identity: str, receipt_identity: str, *, _trust: object) -> None:
        if _trust is not _TRUST:
            raise ValueError("runtime approval must originate from a verified database receipt")
        self.artifact_hash = artifact_identity
        self.receipt_hash = receipt_identity


def receipt_hash(value: dict) -> str:
    """Hash canonical operator intent without its own hash or DB-created timestamp."""
    return digest({key: item for key, item in value.items() if key != "receipt_hash"})


def _manual_requirements(artifact: dict, receipt: ActivationReceipt) -> None:
    model = RerankArtifact.model_validate(artifact)
    evidence, ack = receipt.review_evidence, receipt.acknowledgements
    if (model.content_hash != receipt.artifact_hash or model.content_hash != artifact_hash(artifact)
            or model.tail_gap is not None or model.gate.get("protocol_valid") is not True
            or model.gate.get("label_validation_passed") is not True
            or model.gate.get("passed") is not False or model.training.get("test_only") is not True
            or model.reviews.get("factual_issues") != [] or evidence is None or ack is None
            or any(value is not True for value in ack.model_dump().values())
            or evidence.factual_errors != 0 or evidence.artifact_hash != model.content_hash
            or evidence.dataset_hash != model.dataset_hash):
        raise ValueError("manual rollout requires exact research artifact, sound labels and sort-only evidence")


def validate_receipt(value: dict, artifact: dict) -> dict:
    """Validate immutable intent; activation may waive only the disclosed quality/review gate."""
    receipt = ActivationReceipt.model_validate(value)
    model = RerankArtifact.model_validate(artifact)
    if (not receipt.actor.strip() or not receipt.reason.strip() or receipt.receipt_hash != receipt_hash(value)
            or receipt.artifact_hash != model.content_hash or receipt.artifact_hash != artifact_hash(artifact)
            or receipt.gate_snapshot != model.gate or receipt.reviews_snapshot != model.reviews
            or receipt.training_test_only is not model.training.get("test_only", False)
            or receipt.evaluation_hash != model.evaluation_hash):
        raise ValueError("operator receipt identity or original evidence snapshot differs")
    if receipt.action == "activate":
        _manual_requirements(artifact, receipt)
    return receipt.model_dump(mode="json")


def make_receipt(artifact: dict, *, action: Literal["activate", "deactivate"], actor: str, reason: str,
                 acknowledgements: dict | None = None, review_evidence: dict | None = None) -> dict:
    """Prepare auditable intent without database writes, activation, training or provider calls."""
    artifact = validate_artifact(artifact)
    value = {"version": _RECEIPT_VERSION, "action": action, "artifact_hash": artifact["content_hash"],
        "actor": actor, "reason": reason, "scope": "sort_only", "gate_snapshot": deepcopy(artifact["gate"]),
        "reviews_snapshot": deepcopy(artifact["reviews"]), "training_test_only": artifact["training"].get("test_only", False),
        "evaluation_hash": artifact["evaluation_hash"], "acknowledgements": acknowledgements, "review_evidence": review_evidence}
    value["receipt_hash"] = receipt_hash(value)
    return validate_receipt(value, artifact)


def _approval_from_db(row: dict | None, artifact: dict) -> DbManualApproval:
    """Verify a store-selected receipt row and mint runtime authority; never accepts caller metadata."""
    if row is None:
        raise ValueError("manual_approval_absent")
    receipt = validate_receipt(row["receipt"], artifact)
    if (row["receipt_hash"] != receipt["receipt_hash"] or row["artifact_hash"] != receipt["artifact_hash"]
            or row["action"] != "activate" or receipt["action"] != "activate"):
        raise ValueError("manual_approval_invalid")
    return DbManualApproval(receipt["artifact_hash"], receipt["receipt_hash"], _trust=_TRUST)


def read_json(path: Path) -> dict:
    """Read operator JSON strictly, rejecting duplicate fields and non-JSON constants."""
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError("duplicate JSON field")
            output[key] = value
        return output
    def bad_constant(_value):
        raise ValueError("nonfinite JSON value")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant)
    if type(value) is not dict:
        raise ValueError("operator JSON must be an object")
    return value


def packaged_rollout() -> tuple[dict, dict]:
    """Load the reviewed coefficient artifact and explicit hash-pinned operator decision."""
    root = files("matching_engine")
    artifact = json.loads(root.joinpath(PACKAGED_ARTIFACT).read_text(encoding="utf-8"))
    decision = json.loads(root.joinpath(PACKAGED_DECISION).read_text(encoding="utf-8"))
    if (artifact.get("content_hash") != REVIEWED_ARTIFACT_HASH
            or decision.get("receipt_hash") != REVIEWED_RECEIPT_HASH):
        raise ValueError("packaged artifact or decision differs from the reviewed identity")
    return validate_artifact(artifact), validate_receipt(decision, artifact)


def bootstrap_packaged(store) -> dict:
    """Apply the packaged decision only to a never-activated DB; respect later rollback/deactivation."""
    artifact, decision = packaged_rollout()
    return store.activate_manual(artifact, decision, only_if_uninitialized=True)


def main(argv: list[str] | None = None) -> int:
    """Operator CLI writes atomic approval/queue transactions; no public activation HTTP exists."""
    from matching_engine.rerank_store import RerankStore

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap-packaged")
    activate = commands.add_parser("activate")
    activate.add_argument("--artifact", type=Path, required=True)
    activate.add_argument("--receipt", type=Path, required=True)
    activate.add_argument("--expected-artifact-hash", required=True)
    deactivate = commands.add_parser("deactivate")
    deactivate.add_argument("--expected-artifact-hash", required=True)
    deactivate.add_argument("--actor", required=True)
    deactivate.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required; no database was modified")
    store = RerankStore(database_url)
    if args.command == "bootstrap-packaged":
        result = bootstrap_packaged(store)
    elif args.command == "activate":
        artifact = read_json(args.artifact)
        if artifact.get("content_hash") != args.expected_artifact_hash:
            raise ValueError("operator expected artifact identity differs")
        result = store.activate_manual(artifact, read_json(args.receipt))
    else:
        result = store.deactivate(args.expected_artifact_hash, actor=args.actor, reason=args.reason)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
