from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import (
    ExecutionCapability,
    ExecutionLease,
    ExecutionProvider,
    ExecutionRequest,
    ImmutableInputSource,
    NetworkMode,
    ProviderDescriptor,
    ProviderProbe,
)


class ExecutionResolutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedExecutionProvider:
    provider: ExecutionProvider
    descriptor: ProviderDescriptor
    probe: ProviderProbe


def _persistence_capability(request: ExecutionRequest) -> ExecutionCapability:
    return {
        "ephemeral": ExecutionCapability.EPHEMERAL,
        "checkpointed": ExecutionCapability.CHECKPOINTED,
        "persistent": ExecutionCapability.PERSISTENT,
    }[request.persistence.value]


def _candidate_eligible(
    request: ExecutionRequest,
    descriptor: ProviderDescriptor,
    probe: ProviderProbe,
) -> bool:
    if descriptor.provider_id != probe.provider_id or not probe.available:
        return False
    if descriptor.isolation_level < request.isolation_floor:
        return False
    if probe.isolation_level < request.isolation_floor:
        return False
    if request.persistence not in descriptor.persistence_modes:
        return False
    if request.persistence not in probe.persistence_modes:
        return False
    if not request.required_capabilities.issubset(descriptor.declared_capabilities):
        return False
    if not request.required_capabilities.issubset(probe.verified_capabilities):
        return False

    persistence_capability = _persistence_capability(request)
    if persistence_capability not in descriptor.declared_capabilities:
        return False
    if persistence_capability not in probe.verified_capabilities:
        return False

    implied: set[ExecutionCapability] = set()
    if request.immutable_inputs:
        implied.add(ExecutionCapability.IMMUTABLE_INPUT)
    if request.outputs:
        implied.add(ExecutionCapability.OUTPUT_CAPTURE)
    if request.network_policy.mode is NetworkMode.NONE:
        implied.add(ExecutionCapability.NETWORK_NONE)
    else:
        implied.add(ExecutionCapability.BOUNDED_NETWORK)

    if not implied.issubset(descriptor.declared_capabilities):
        return False
    if not implied.issubset(probe.verified_capabilities):
        return False
    return True


def resolve_execution_provider(
    request: ExecutionRequest,
    providers: Iterable[ExecutionProvider],
) -> ResolvedExecutionProvider:
    candidates: list[ResolvedExecutionProvider] = []
    for provider in providers:
        descriptor = provider.descriptor()
        probe = provider.probe()
        if _candidate_eligible(request, descriptor, probe):
            candidates.append(ResolvedExecutionProvider(provider, descriptor, probe))

    if not candidates:
        required = ",".join(sorted(cap.value for cap in request.required_capabilities))
        raise ExecutionResolutionError(
            "no configured execution provider can satisfy request: "
            f"isolation>={request.isolation_floor.name.lower()}, "
            f"persistence={request.persistence.value}, capabilities=[{required}]"
        )

    def score(item: ResolvedExecutionProvider) -> tuple[int, int, int, str]:
        preferred = len(
            request.preferred_capabilities.intersection(
                item.descriptor.declared_capabilities
            )
        )
        return (
            item.descriptor.component_count,
            -preferred,
            -item.descriptor.priority,
            item.descriptor.provider_id,
        )

    candidates.sort(key=score)
    return candidates[0]


def validate_execution_lease(request: ExecutionRequest, lease: ExecutionLease) -> None:
    if lease.request_id != request.request_id:
        raise ExecutionResolutionError("execution lease request_id does not match request")
    if lease.isolation_level < request.isolation_floor:
        raise ExecutionResolutionError("execution lease does not meet required isolation floor")
    if lease.persistence is not request.persistence:
        raise ExecutionResolutionError("execution lease persistence does not match request")

    persistence_capability = _persistence_capability(request)
    if persistence_capability not in lease.capabilities:
        raise ExecutionResolutionError(
            "execution lease is missing persistence capability "
            f"{persistence_capability.value}"
        )

    missing = request.required_capabilities - lease.capabilities
    if missing:
        raise ExecutionResolutionError(
            "execution lease is missing required capabilities: "
            + ",".join(sorted(cap.value for cap in missing))
        )

    requested = frozenset(request.immutable_inputs)
    observed = frozenset(lease.materialized_inputs)
    if requested != observed:
        missing_inputs = requested - observed
        unexpected_inputs = observed - requested
        details = []
        if missing_inputs:
            details.append(
                "missing="
                + ",".join(sorted(f"{ref.input_id}:{ref.digest}" for ref in missing_inputs))
            )
        if unexpected_inputs:
            details.append(
                "unexpected="
                + ",".join(sorted(f"{ref.input_id}:{ref.digest}" for ref in unexpected_inputs))
            )
        raise ExecutionResolutionError(
            "execution lease immutable-input attestation mismatch"
            + (": " + " ".join(details) if details else "")
        )

    if lease.work_policy != request.work_policy:
        raise ExecutionResolutionError("execution lease work policy does not match request")
    if lease.output_policy != request.output_policy:
        raise ExecutionResolutionError("execution lease output policy does not match request")
    if lease.network_policy.mode is not request.network_policy.mode:
        raise ExecutionResolutionError("execution lease network mode does not match request")
    if tuple(lease.network_policy.allowed_bindings) != tuple(request.network_policy.allowed_bindings):
        raise ExecutionResolutionError("execution lease network bindings do not match request")

    if ExecutionCapability.EXEC in lease.capabilities and lease.ports.execution is None:
        raise ExecutionResolutionError(
            "execution lease claims execution capability without execution port"
        )
    if request.immutable_inputs and ExecutionCapability.IMMUTABLE_INPUT not in lease.capabilities:
        raise ExecutionResolutionError("execution lease lacks immutable-input capability")
    if request.outputs:
        if ExecutionCapability.OUTPUT_CAPTURE not in lease.capabilities:
            raise ExecutionResolutionError("execution lease lacks output-capture capability")
        if lease.ports.evidence is None:
            raise ExecutionResolutionError(
                "execution lease lacks evidence port for declared outputs"
            )
    if ExecutionCapability.STDIO_CAPTURE in lease.capabilities and lease.ports.evidence is None:
        raise ExecutionResolutionError(
            "execution lease claims stdio evidence without evidence port"
        )
    if request.network_policy.mode is NetworkMode.NONE:
        if ExecutionCapability.NETWORK_NONE not in lease.capabilities:
            raise ExecutionResolutionError(
                "network=none request requires acquired network-none capability"
            )
    elif ExecutionCapability.BOUNDED_NETWORK not in lease.capabilities:
        raise ExecutionResolutionError(
            "bounded network request requires acquired bounded-network capability"
        )


def _release_after_failure(
    resolved: ResolvedExecutionProvider,
    lease: ExecutionLease,
    *,
    message: str,
) -> None:
    try:
        resolved.provider.release(lease)
    except BaseException as cleanup_exc:
        raise ExecutionResolutionError(message + " and cleanup was uncertain") from cleanup_exc


def acquire_validated_execution(
    request: ExecutionRequest,
    resolved: ResolvedExecutionProvider,
    *,
    input_source: ImmutableInputSource | None = None,
) -> ExecutionLease:
    if request.immutable_inputs and input_source is None:
        raise ExecutionResolutionError(
            "immutable inputs require a trusted service-side input source"
        )

    lease = resolved.provider.acquire(request, input_source=input_source)
    try:
        validate_execution_lease(request, lease)
    except BaseException:
        _release_after_failure(
            resolved,
            lease,
            message="acquired execution failed validation",
        )
        raise
    return lease
