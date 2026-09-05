from __future__ import annotations

import json
from typing import Any

from .contracts import (
    AppliedNetworkPolicy,
    BoundedStatePolicy,
    ExecutionCommandResult,
    ExecutionLease,
    ExecutionReceipt,
    ExecutionRequest,
    ImmutableInputRef,
    OutputArtifactRef,
    OutputSpec,
    ProviderBinding,
    WorkloadIdentity,
)


def _workload(value: WorkloadIdentity) -> dict[str, object]:
    return {
        "kind": value.kind,
        "runtime_identity": value.runtime_identity,
        "entrypoint": value.entrypoint,
        "argv": list(value.argv),
        "working_directory_class": value.working_directory_class,
    }


def _input(value: ImmutableInputRef) -> dict[str, object]:
    result: dict[str, object] = {
        "input_id": value.input_id,
        "digest": value.digest,
        "size_bytes": value.size_bytes,
        "presentation_path": value.presentation_path,
        "read_only": value.read_only,
    }
    if value.media_type is not None:
        result["media_type"] = value.media_type
    return result


def _output_spec(value: OutputSpec) -> dict[str, object]:
    result: dict[str, object] = {
        "output_id": value.output_id,
        "presentation_path": value.presentation_path,
        "max_bytes": value.max_bytes,
        "required": value.required,
    }
    if value.media_type is not None:
        result["media_type"] = value.media_type
    return result


def _output_ref(value: OutputArtifactRef) -> dict[str, object]:
    result: dict[str, object] = {
        "output_id": value.output_id,
        "digest": value.digest,
        "size_bytes": value.size_bytes,
    }
    if value.media_type is not None:
        result["media_type"] = value.media_type
    return result


def _bounded(value: BoundedStatePolicy) -> dict[str, int]:
    return {
        "max_bytes": value.max_bytes,
        "max_files": value.max_files,
        "max_directories": value.max_directories,
    }


def _network(value: AppliedNetworkPolicy | object) -> dict[str, object]:
    mode = getattr(value, "mode")
    bindings = getattr(value, "allowed_bindings")
    return {
        "mode": mode.value,
        "allowed_bindings": list(bindings),
    }


def _binding(value: ProviderBinding) -> dict[str, object]:
    return {
        "provider_id": value.provider_id,
        "provider_kind": value.provider_kind,
        "provider_version": value.provider_version,
        "provider_runtime_id": value.provider_runtime_id,
        "roles": sorted(item.value for item in value.roles),
        "capabilities": sorted(item.value for item in value.capabilities),
        "metadata": dict(value.metadata),
    }


def _command(value: ExecutionCommandResult | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "argv": list(value.argv),
        "returncode": value.returncode,
        "stdout": value.stdout,
        "stderr": value.stderr,
    }


def request_to_dict(value: ExecutionRequest) -> dict[str, object]:
    return {
        "schema": value.schema,
        "request_id": value.request_id,
        "workload": _workload(value.workload),
        "required_capabilities": sorted(
            item.value for item in value.required_capabilities
        ),
        "preferred_capabilities": sorted(
            item.value for item in value.preferred_capabilities
        ),
        "isolation_floor": value.isolation_floor.name.lower(),
        "persistence": value.persistence.value,
        "immutable_inputs": [_input(item) for item in value.immutable_inputs],
        "outputs": [_output_spec(item) for item in value.outputs],
        "work_policy": _bounded(value.work_policy),
        "output_policy": _bounded(value.output_policy),
        "network_policy": {
            "mode": value.network_policy.mode.value,
            "allowed_bindings": list(value.network_policy.allowed_bindings),
        },
        "resource_requirements": dict(value.resource_requirements),
        "timeout_seconds": value.timeout_seconds,
        "environment_policy": {
            "allowed_keys": list(value.environment_policy.allowed_keys),
            "secret_bindings": list(value.environment_policy.secret_bindings),
        },
        "evidence_requirements": dict(value.evidence_requirements),
    }


def lease_to_dict(value: ExecutionLease) -> dict[str, object]:
    return {
        "schema": value.schema,
        "request_id": value.request_id,
        "lease_id": value.lease_id,
        "providers": [_binding(item) for item in value.bindings],
        "capabilities": sorted(item.value for item in value.capabilities),
        "isolation_level": value.isolation_level.name.lower(),
        "persistence": value.persistence.value,
        "materialized_inputs": [_input(item) for item in value.materialized_inputs],
        "network_policy": _network(value.network_policy),
        "work_policy": _bounded(value.work_policy),
        "output_policy": _bounded(value.output_policy),
        "owned_resource_id": value.owned_resource_id,
        "metadata": dict(value.metadata),
    }


def receipt_to_dict(value: ExecutionReceipt) -> dict[str, object]:
    return {
        "schema": value.schema,
        "request_id": value.request_id,
        "lease_id": value.lease_id,
        "execution_id": value.execution_id,
        "outcome": value.outcome.value,
        "providers": [_binding(item) for item in value.bindings],
        "workload": _workload(value.workload),
        "immutable_inputs": [_input(item) for item in value.immutable_inputs],
        "isolation_level": value.isolation_level.name.lower(),
        "network_policy": _network(value.network_policy),
        "command": _command(value.command),
        "cleanup_state": value.cleanup_state.value,
        "outputs": [_output_ref(item) for item in value.outputs],
        "diagnostic_codes": list(value.diagnostic_codes),
        "metadata": dict(value.metadata),
    }


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
