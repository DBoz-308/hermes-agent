from __future__ import annotations

import hashlib

from agent.execution.contracts import (
    LEASE_SCHEMA,
    RECEIPT_SCHEMA,
    REQUEST_SCHEMA,
    AppliedNetworkPolicy,
    BoundedStatePolicy,
    CleanupState,
    ExecutionCapability,
    ExecutionCommandResult,
    ExecutionLease,
    ExecutionOutcome,
    ExecutionPorts,
    ExecutionReceipt,
    ExecutionRequest,
    ImmutableInputRef,
    IsolationLevel,
    NetworkMode,
    NetworkPolicy,
    OutputArtifactRef,
    OutputSpec,
    PersistenceMode,
    ProviderBinding,
    ProviderRole,
    WorkloadIdentity,
)
from agent.execution.schema_documents import schema_document
from agent.execution.serialization import (
    canonical_json_bytes,
    lease_to_dict,
    receipt_to_dict,
    request_to_dict,
)


def _sample_contracts() -> tuple[ExecutionRequest, ExecutionLease, ExecutionReceipt]:
    payload = b"input"
    immutable_input = ImmutableInputRef(
        input_id="input-1",
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        presentation_path="inputs/input.wav",
        media_type="audio/wav",
    )
    output = OutputSpec(
        output_id="analysis",
        presentation_path="outputs/analysis.json",
        max_bytes=512,
        media_type="application/json",
    )
    bounds = BoundedStatePolicy(max_bytes=1024, max_files=8, max_directories=2)
    capabilities = frozenset(
        {
            ExecutionCapability.EXEC,
            ExecutionCapability.EPHEMERAL,
            ExecutionCapability.STRONG_ISOLATION,
            ExecutionCapability.IMMUTABLE_INPUT,
            ExecutionCapability.NETWORK_NONE,
            ExecutionCapability.OWNED_DESTROY,
            ExecutionCapability.PROVIDER_PROVENANCE,
            ExecutionCapability.STDIO_CAPTURE,
            ExecutionCapability.OUTPUT_CAPTURE,
        }
    )
    workload = WorkloadIdentity(
        kind="container-image",
        runtime_identity="example.invalid/tool@sha256:" + "b" * 64,
        entrypoint="tool",
        argv=("--input", "inputs/input.wav"),
    )
    request = ExecutionRequest(
        request_id="request-1",
        workload=workload,
        required_capabilities=capabilities,
        isolation_floor=IsolationLevel.STRONG,
        persistence=PersistenceMode.EPHEMERAL,
        immutable_inputs=(immutable_input,),
        outputs=(output,),
        work_policy=bounds,
        output_policy=bounds,
        network_policy=NetworkPolicy(mode=NetworkMode.NONE),
        timeout_seconds=30.0,
    )
    binding = ProviderBinding(
        provider_id="provider-1",
        provider_kind="fake",
        provider_version="1",
        provider_runtime_id="runtime-1",
        roles=frozenset(
            {
                ProviderRole.ISOLATION,
                ProviderRole.EXECUTION,
                ProviderRole.INPUT,
                ProviderRole.EVIDENCE,
            }
        ),
        capabilities=capabilities,
    )
    lease = ExecutionLease(
        request_id=request.request_id,
        lease_id="lease-1",
        bindings=(binding,),
        capabilities=capabilities,
        isolation_level=IsolationLevel.STRONG,
        persistence=PersistenceMode.EPHEMERAL,
        materialized_inputs=(immutable_input,),
        network_policy=AppliedNetworkPolicy(mode=NetworkMode.NONE),
        work_policy=bounds,
        output_policy=bounds,
        owned_resource_id="resource-1",
        ports=ExecutionPorts(),
    )
    output_bytes = b'{"ok":true}'
    receipt = ExecutionReceipt(
        request_id=request.request_id,
        lease_id=lease.lease_id,
        execution_id="execution-1",
        outcome=ExecutionOutcome.PASS,
        bindings=lease.bindings,
        workload=workload,
        immutable_inputs=lease.materialized_inputs,
        isolation_level=lease.isolation_level,
        network_policy=lease.network_policy,
        command=ExecutionCommandResult(
            argv=("tool", "--input", "inputs/input.wav"),
            returncode=0,
            stdout="ok\n",
        ),
        cleanup_state=CleanupState.CLEAN,
        outputs=(
            OutputArtifactRef(
                output_id="analysis",
                digest="sha256:" + hashlib.sha256(output_bytes).hexdigest(),
                size_bytes=len(output_bytes),
                media_type="application/json",
            ),
        ),
    )
    return request, lease, receipt


def test_schema_documents_are_strict_and_versioned() -> None:
    for schema_id in (REQUEST_SCHEMA, LEASE_SCHEMA, RECEIPT_SCHEMA):
        schema = schema_document(schema_id)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == schema_id
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema"]["const"] == schema_id


def test_serialized_top_level_fields_match_schema_contracts() -> None:
    request, lease, receipt = _sample_contracts()
    cases = (
        (REQUEST_SCHEMA, request_to_dict(request)),
        (LEASE_SCHEMA, lease_to_dict(lease)),
        (RECEIPT_SCHEMA, receipt_to_dict(receipt)),
    )
    for schema_id, document in cases:
        schema = schema_document(schema_id)
        assert set(document) == set(schema["required"])
        assert set(document) == set(schema["properties"])
        assert document["schema"] == schema_id


def test_capability_enums_cover_every_generic_capability() -> None:
    expected = {item.value for item in ExecutionCapability}
    request_schema = schema_document(REQUEST_SCHEMA)
    lease_schema = schema_document(LEASE_SCHEMA)
    receipt_schema = schema_document(RECEIPT_SCHEMA)

    assert set(
        request_schema["properties"]["required_capabilities"]["items"]["enum"]
    ) == expected
    assert set(lease_schema["properties"]["capabilities"]["items"]["enum"]) == expected
    assert set(
        receipt_schema["properties"]["providers"]["items"]["properties"][
            "capabilities"
        ]["items"]["enum"]
    ) == expected


def test_input_and_output_schema_are_content_addressed_and_bounded() -> None:
    request_schema = schema_document(REQUEST_SCHEMA)
    input_schema = request_schema["properties"]["immutable_inputs"]["items"]
    output_schema = request_schema["properties"]["outputs"]["items"]
    assert "size_bytes" in input_schema["required"]
    assert input_schema["properties"]["digest"]["pattern"].startswith("^sha256:")
    assert output_schema["properties"]["max_bytes"]["minimum"] == 1

    receipt_schema = schema_document(RECEIPT_SCHEMA)
    receipt_output = receipt_schema["properties"]["outputs"]["items"]
    assert receipt_output["properties"]["digest"]["pattern"].startswith("^sha256:")
    assert "size_bytes" in receipt_output["required"]


def test_schema_documents_are_lab_neutral_and_source_free() -> None:
    encoded = b"".join(
        canonical_json_bytes(schema_document(schema_id))
        for schema_id in (REQUEST_SCHEMA, LEASE_SCHEMA, RECEIPT_SCHEMA)
    ).lower()
    assert b"hermes_lab" not in encoded
    assert b"scenario" not in encoded
    assert b"assertion" not in encoded
    assert b"hermes_artifact" not in encoded
    assert b"host_path" not in encoded
    assert b"source_path" not in encoded


def test_schema_document_returns_isolated_copy() -> None:
    one = schema_document(REQUEST_SCHEMA)
    two = schema_document(REQUEST_SCHEMA)
    one["properties"]["request_id"]["minLength"] = 99
    assert two["properties"]["request_id"]["minLength"] == 1
