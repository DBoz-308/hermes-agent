from __future__ import annotations

from dataclasses import replace
import hashlib
import io

import pytest

from agent.execution import (
    AppliedNetworkPolicy,
    BoundedStatePolicy,
    CleanupState,
    EnvironmentPolicy,
    ExecutionCapability,
    ExecutionCommandResult,
    ExecutionLease,
    ExecutionOutcome,
    ExecutionPorts,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionResolutionError,
    ImmutableInputRef,
    IsolationLevel,
    NetworkMode,
    NetworkPolicy,
    OutputArtifactRef,
    OutputSpec,
    PersistenceMode,
    ProviderBinding,
    ProviderDescriptor,
    ProviderProbe,
    ProviderRole,
    WorkloadIdentity,
    acquire_validated_execution,
    canonical_json_bytes,
    lease_to_dict,
    receipt_to_dict,
    request_to_dict,
    resolve_execution_provider,
)


INPUT_BYTES = b"fixed-audio-input"
INPUT_DIGEST = "sha256:" + hashlib.sha256(INPUT_BYTES).hexdigest()
OUTPUT_BYTES = b'{"ok":true}\n'
OUTPUT_DIGEST = "sha256:" + hashlib.sha256(OUTPUT_BYTES).hexdigest()

REQUIRED = frozenset(
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
ROLES = frozenset(
    {
        ProviderRole.ISOLATION,
        ProviderRole.EXECUTION,
        ProviderRole.INPUT,
        ProviderRole.EVIDENCE,
    }
)
INPUT = ImmutableInputRef(
    input_id="audio",
    digest=INPUT_DIGEST,
    size_bytes=len(INPUT_BYTES),
    presentation_path="inputs/audio.wav",
    media_type="audio/wav",
)
OUTPUT_SPEC = OutputSpec(
    output_id="analysis",
    presentation_path="outputs/analysis.json",
    max_bytes=2_000_000,
    media_type="application/json",
)
WORK = BoundedStatePolicy(max_bytes=4_000_000, max_files=32, max_directories=8)
OUTPUT = BoundedStatePolicy(max_bytes=2_000_000, max_files=16, max_directories=4)


class _InputSource:
    def open_input(self, ref: ImmutableInputRef):
        assert ref == INPUT
        return io.BytesIO(INPUT_BYTES)


class _Execution:
    def exec(
        self,
        lease: ExecutionLease,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionCommandResult:
        del lease, timeout_seconds
        return ExecutionCommandResult(argv=tuple(argv), returncode=0, stdout="ok\n")


class _Fixture:
    def materialize_inputs(
        self,
        lease: ExecutionLease,
        inputs: tuple[ImmutableInputRef, ...],
        source: _InputSource,
    ) -> tuple[ImmutableInputRef, ...]:
        del lease, source
        return tuple(inputs)


class _Evidence:
    def collect(self, lease: ExecutionLease) -> dict[str, object]:
        return {"lease_id": lease.lease_id}

    def collect_output(self, lease: ExecutionLease, output: OutputSpec) -> bytes:
        del lease
        assert output == OUTPUT_SPEC
        return OUTPUT_BYTES


PORTS = ExecutionPorts(
    execution=_Execution(),
    fixture=_Fixture(),
    evidence=_Evidence(),
)


def _binding(
    *,
    provider_id: str = "fake-strong",
    capabilities: frozenset[ExecutionCapability] = REQUIRED,
) -> ProviderBinding:
    return ProviderBinding(
        provider_id=provider_id,
        provider_kind="fake",
        provider_version="1",
        provider_runtime_id=f"runtime:{provider_id}",
        roles=ROLES,
        capabilities=capabilities,
        metadata={"profile": "test"},
    )


@pytest.fixture
def execution_request() -> ExecutionRequest:
    return ExecutionRequest(
        request_id="request-1",
        workload=WorkloadIdentity(
            kind="container-image",
            runtime_identity="example.invalid/image@sha256:" + "b" * 64,
            entrypoint="tool",
            argv=("--input", "inputs/audio.wav"),
        ),
        required_capabilities=REQUIRED,
        isolation_floor=IsolationLevel.STRONG,
        persistence=PersistenceMode.EPHEMERAL,
        immutable_inputs=(INPUT,),
        outputs=(OUTPUT_SPEC,),
        work_policy=WORK,
        output_policy=OUTPUT,
        network_policy=NetworkPolicy(mode=NetworkMode.NONE),
        timeout_seconds=60.0,
        environment_policy=EnvironmentPolicy(allowed_keys=("LANG",)),
        evidence_requirements={"stdio": "required"},
    )


def _lease(
    execution_request: ExecutionRequest,
    *,
    provider_id: str = "fake-strong",
    isolation_level: IsolationLevel = IsolationLevel.STRONG,
    capabilities: frozenset[ExecutionCapability] = REQUIRED,
    materialized_inputs: tuple[ImmutableInputRef, ...] = (),
) -> ExecutionLease:
    return ExecutionLease(
        request_id=execution_request.request_id,
        lease_id=f"lease:{provider_id}",
        bindings=(_binding(provider_id=provider_id, capabilities=capabilities),),
        capabilities=capabilities,
        isolation_level=isolation_level,
        persistence=execution_request.persistence,
        materialized_inputs=materialized_inputs,
        network_policy=AppliedNetworkPolicy(
            mode=execution_request.network_policy.mode,
            allowed_bindings=execution_request.network_policy.allowed_bindings,
        ),
        work_policy=execution_request.work_policy,
        output_policy=execution_request.output_policy,
        owned_resource_id=f"resource:{provider_id}",
        ports=PORTS,
    )


class _Provider:
    def __init__(
        self,
        execution_request: ExecutionRequest,
        *,
        provider_id: str = "fake-strong",
        descriptor_isolation: IsolationLevel = IsolationLevel.STRONG,
        probe_isolation: IsolationLevel = IsolationLevel.STRONG,
        lease_isolation: IsolationLevel = IsolationLevel.STRONG,
        capabilities: frozenset[ExecutionCapability] = REQUIRED,
        release_raises: bool = False,
    ) -> None:
        self.execution_request = execution_request
        self.provider_id = provider_id
        self.descriptor_isolation = descriptor_isolation
        self.probe_isolation = probe_isolation
        self.lease_isolation = lease_isolation
        self.capabilities = capabilities
        self.release_raises = release_raises
        self.acquire_count = 0
        self.release_count = 0

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_kind="fake",
            roles=ROLES,
            declared_capabilities=self.capabilities,
            isolation_level=self.descriptor_isolation,
            persistence_modes=frozenset({PersistenceMode.EPHEMERAL}),
        )

    def probe(self) -> ProviderProbe:
        return ProviderProbe(
            provider_id=self.provider_id,
            available=True,
            provider_version="1",
            verified_capabilities=self.capabilities,
            isolation_level=self.probe_isolation,
            persistence_modes=frozenset({PersistenceMode.EPHEMERAL}),
        )

    def acquire(
        self,
        request: ExecutionRequest,
        *,
        input_source: _InputSource | None = None,
    ) -> ExecutionLease:
        assert request is self.execution_request
        self.acquire_count += 1
        materialized: tuple[ImmutableInputRef, ...] = ()
        if request.immutable_inputs:
            assert input_source is not None
            refs: list[ImmutableInputRef] = []
            for ref in request.immutable_inputs:
                with input_source.open_input(ref) as stream:
                    value = stream.read()
                assert len(value) == ref.size_bytes
                assert "sha256:" + hashlib.sha256(value).hexdigest() == ref.digest
                refs.append(ref)
            materialized = tuple(refs)
        return _lease(
            request,
            provider_id=self.provider_id,
            isolation_level=self.lease_isolation,
            capabilities=self.capabilities,
            materialized_inputs=materialized,
        )

    def release(self, lease: ExecutionLease) -> None:
        del lease
        self.release_count += 1
        if self.release_raises:
            raise RuntimeError("cleanup uncertain")


def test_strong_request_rejects_weak_provider_candidate(
    execution_request: ExecutionRequest,
) -> None:
    provider = _Provider(
        execution_request,
        descriptor_isolation=IsolationLevel.STATE_ONLY,
        probe_isolation=IsolationLevel.STATE_ONLY,
        capabilities=REQUIRED - {ExecutionCapability.STRONG_ISOLATION},
    )
    with pytest.raises(ExecutionResolutionError, match="no configured execution provider"):
        resolve_execution_provider(execution_request, [provider])
    assert provider.acquire_count == 0


def test_network_none_is_candidate_capability_not_post_acquire_guess(
    execution_request: ExecutionRequest,
) -> None:
    capabilities = REQUIRED - {ExecutionCapability.NETWORK_NONE}
    provider = _Provider(execution_request, capabilities=capabilities)
    with pytest.raises(ExecutionResolutionError, match="no configured execution provider"):
        resolve_execution_provider(execution_request, [provider])
    assert provider.acquire_count == 0


def test_valid_provider_materializes_inputs_during_acquisition_then_revalidates(
    execution_request: ExecutionRequest,
) -> None:
    provider = _Provider(execution_request)
    resolved = resolve_execution_provider(execution_request, [provider])
    lease = acquire_validated_execution(
        execution_request,
        resolved,
        input_source=_InputSource(),
    )
    assert lease.request_id == execution_request.request_id
    assert lease.materialized_inputs == execution_request.immutable_inputs
    assert provider.acquire_count == 1
    assert provider.release_count == 0


def test_missing_input_source_fails_before_effectful_acquisition(
    execution_request: ExecutionRequest,
) -> None:
    provider = _Provider(execution_request)
    resolved = resolve_execution_provider(execution_request, [provider])
    with pytest.raises(ExecutionResolutionError, match="trusted service-side input source"):
        acquire_validated_execution(execution_request, resolved)
    assert provider.acquire_count == 0
    assert provider.release_count == 0


def test_descriptor_and_probe_cannot_substitute_for_weaker_acquired_lease(
    execution_request: ExecutionRequest,
) -> None:
    provider = _Provider(execution_request, lease_isolation=IsolationLevel.STATE_ONLY)
    resolved = resolve_execution_provider(execution_request, [provider])
    with pytest.raises(ExecutionResolutionError, match="isolation floor"):
        acquire_validated_execution(
            execution_request,
            resolved,
            input_source=_InputSource(),
        )
    assert provider.acquire_count == 1
    assert provider.release_count == 1


def test_exact_immutable_input_digest_is_enforced_at_contract_boundary() -> None:
    with pytest.raises(ValueError, match="sha256"):
        replace(INPUT, digest="not-a-digest")


def test_cleanup_uncertainty_overrides_validation_failure(
    execution_request: ExecutionRequest,
) -> None:
    provider = _Provider(
        execution_request,
        lease_isolation=IsolationLevel.STATE_ONLY,
        release_raises=True,
    )
    resolved = resolve_execution_provider(execution_request, [provider])
    with pytest.raises(ExecutionResolutionError, match="cleanup was uncertain"):
        acquire_validated_execution(
            execution_request,
            resolved,
            input_source=_InputSource(),
        )
    assert provider.release_count == 1


def test_invalid_effectful_acquisition_does_not_fall_through_to_second_provider(
    execution_request: ExecutionRequest,
) -> None:
    bad = _Provider(
        execution_request,
        provider_id="a-bad",
        lease_isolation=IsolationLevel.STATE_ONLY,
    )
    good = _Provider(execution_request, provider_id="b-good")
    resolved = resolve_execution_provider(execution_request, [bad, good])
    assert resolved.descriptor.provider_id == "a-bad"
    with pytest.raises(ExecutionResolutionError, match="isolation floor"):
        acquire_validated_execution(
            execution_request,
            resolved,
            input_source=_InputSource(),
        )
    assert bad.acquire_count == 1
    assert bad.release_count == 1
    assert good.acquire_count == 0


def test_declared_outputs_must_fit_global_output_bounds() -> None:
    with pytest.raises(ValueError, match="output_policy.max_bytes"):
        ExecutionRequest(
            request_id="too-large",
            workload=WorkloadIdentity(
                kind="container-image",
                runtime_identity="example.invalid/image@sha256:" + "b" * 64,
                entrypoint="tool",
            ),
            outputs=(
                OutputSpec(
                    output_id="oversized",
                    presentation_path="outputs/large",
                    max_bytes=101,
                ),
            ),
            output_policy=BoundedStatePolicy(
                max_bytes=100,
                max_files=1,
                max_directories=1,
            ),
        )


def test_pass_receipt_requires_clean_cleanup(
    execution_request: ExecutionRequest,
) -> None:
    lease = _lease(execution_request, materialized_inputs=(INPUT,))
    with pytest.raises(ValueError, match="PASS requires clean cleanup"):
        ExecutionReceipt(
            request_id=execution_request.request_id,
            lease_id=lease.lease_id,
            execution_id="execution-1",
            outcome=ExecutionOutcome.PASS,
            bindings=lease.bindings,
            workload=execution_request.workload,
            immutable_inputs=lease.materialized_inputs,
            isolation_level=lease.isolation_level,
            network_policy=lease.network_policy,
            command=ExecutionCommandResult(argv=("tool",), returncode=0),
            cleanup_state=CleanupState.UNCERTAIN,
        )


def test_input_and_output_paths_cannot_be_host_absolute_or_traversing() -> None:
    with pytest.raises(ValueError, match="lease-relative"):
        replace(INPUT, presentation_path="/home/operator/audio.wav")
    with pytest.raises(ValueError, match="normalized"):
        replace(INPUT, presentation_path="inputs/../operator/audio.wav")
    with pytest.raises(ValueError, match="lease-relative"):
        replace(OUTPUT_SPEC, presentation_path="/tmp/analysis.json")


def test_serialization_is_stable_source_free_and_lab_neutral(
    execution_request: ExecutionRequest,
) -> None:
    lease = _lease(execution_request, materialized_inputs=(INPUT,))
    receipt = ExecutionReceipt(
        request_id=execution_request.request_id,
        lease_id=lease.lease_id,
        execution_id="execution-1",
        outcome=ExecutionOutcome.PASS,
        bindings=lease.bindings,
        workload=execution_request.workload,
        immutable_inputs=lease.materialized_inputs,
        isolation_level=lease.isolation_level,
        network_policy=lease.network_policy,
        command=ExecutionCommandResult(
            argv=(execution_request.workload.entrypoint,),
            returncode=0,
            stdout="ok\n",
        ),
        cleanup_state=CleanupState.CLEAN,
        outputs=(
            OutputArtifactRef(
                output_id="analysis",
                digest=OUTPUT_DIGEST,
                size_bytes=len(OUTPUT_BYTES),
                media_type="application/json",
            ),
        ),
    )

    request_bytes = canonical_json_bytes(request_to_dict(execution_request))
    lease_bytes = canonical_json_bytes(lease_to_dict(lease))
    receipt_bytes = canonical_json_bytes(receipt_to_dict(receipt))
    assert request_bytes == canonical_json_bytes(request_to_dict(execution_request))
    assert lease_bytes == canonical_json_bytes(lease_to_dict(lease))
    assert receipt_bytes == canonical_json_bytes(receipt_to_dict(receipt))
    combined = (request_bytes + lease_bytes + receipt_bytes).lower()
    assert b"hermes_lab" not in combined
    assert b"scenario" not in combined
    assert b"assertion" not in combined
    assert b"hermes_artifact" not in combined
    assert b"/home/" not in combined
    assert b"inputsource" not in combined
