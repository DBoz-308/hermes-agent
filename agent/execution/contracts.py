from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import re
from typing import BinaryIO, Mapping, Protocol, Sequence, runtime_checkable


REQUEST_SCHEMA = "hermes.execution.request.v1"
LEASE_SCHEMA = "hermes.execution.lease.v1"
RECEIPT_SCHEMA = "hermes.execution.receipt.v1"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class IsolationLevel(IntEnum):
    NONE = 0
    STATE_ONLY = 10
    STRONG = 20


class PersistenceMode(str, Enum):
    EPHEMERAL = "ephemeral"
    CHECKPOINTED = "checkpointed"
    PERSISTENT = "persistent"


class NetworkMode(str, Enum):
    NONE = "none"
    BOUNDED = "bounded"


class ExecutionCapability(str, Enum):
    EXEC = "execution.exec"
    EPHEMERAL = "execution.ephemeral"
    CHECKPOINTED = "execution.checkpointed"
    PERSISTENT = "execution.persistent"
    STRONG_ISOLATION = "isolation.strong"
    IMMUTABLE_INPUT = "input.immutable"
    NETWORK_NONE = "network.none"
    BOUNDED_NETWORK = "network.bounded"
    OWNED_DESTROY = "lifecycle.owned_destroy"
    PROVIDER_PROVENANCE = "evidence.provider_provenance"
    STDIO_CAPTURE = "evidence.stdio"
    OUTPUT_CAPTURE = "evidence.output"


class ProviderRole(str, Enum):
    ISOLATION = "isolation"
    EXECUTION = "execution"
    INPUT = "input"
    EVIDENCE = "evidence"
    SNAPSHOT = "snapshot"
    SERVICE_BINDING = "service_binding"


class ExecutionOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    UNCERTAIN = "UNCERTAIN"


class CleanupState(str, Enum):
    CLEAN = "clean"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    NOT_ATTEMPTED = "not_attempted"


def _validate_lease_relative_path(value: str, *, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if value.startswith("/"):
        raise ValueError(f"{field_name} must be lease-relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} must be normalized and lease-relative")


def _validate_sha256(value: str, *, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be lowercase sha256:<64-hex>")


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    kind: str
    runtime_identity: str
    entrypoint: str
    argv: tuple[str, ...] = ()
    working_directory_class: str = "work"

    def __post_init__(self) -> None:
        for name, value in (
            ("kind", self.kind),
            ("runtime_identity", self.runtime_identity),
            ("entrypoint", self.entrypoint),
            ("working_directory_class", self.working_directory_class),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if any("\x00" in item for item in self.argv):
            raise ValueError("argv entries may not contain NUL")


@dataclass(frozen=True, slots=True)
class ImmutableInputRef:
    input_id: str
    digest: str
    size_bytes: int
    presentation_path: str
    media_type: str | None = None
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.input_id or not self.input_id.strip():
            raise ValueError("input_id must be non-empty")
        _validate_sha256(self.digest, field_name="input digest")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise ValueError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not self.read_only:
            raise ValueError("generic immutable inputs must be read-only")
        _validate_lease_relative_path(self.presentation_path, field_name="presentation_path")


@dataclass(frozen=True, slots=True)
class OutputSpec:
    output_id: str
    presentation_path: str
    max_bytes: int
    media_type: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not self.output_id or not self.output_id.strip():
            raise ValueError("output_id must be non-empty")
        _validate_lease_relative_path(
            self.presentation_path,
            field_name="output presentation_path",
        )
        if not isinstance(self.max_bytes, int) or isinstance(self.max_bytes, bool):
            raise ValueError("output max_bytes must be an integer")
        if self.max_bytes < 1:
            raise ValueError("output max_bytes must be positive")


@dataclass(frozen=True, slots=True)
class OutputArtifactRef:
    output_id: str
    digest: str
    size_bytes: int
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not self.output_id or not self.output_id.strip():
            raise ValueError("output_id must be non-empty")
        _validate_sha256(self.digest, field_name="output digest")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise ValueError("output size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("output size_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class BoundedStatePolicy:
    max_bytes: int
    max_files: int
    max_directories: int

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_files, self.max_directories) < 0:
            raise ValueError("bounded state limits must be non-negative")


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    mode: NetworkMode = NetworkMode.NONE
    allowed_bindings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is NetworkMode.NONE and self.allowed_bindings:
            raise ValueError("network=none cannot declare allowed bindings")
        if self.mode is NetworkMode.BOUNDED and not self.allowed_bindings:
            raise ValueError("bounded network requires at least one admitted binding")
        if len(set(self.allowed_bindings)) != len(self.allowed_bindings):
            raise ValueError("network bindings must be unique")


@dataclass(frozen=True, slots=True)
class EnvironmentPolicy:
    allowed_keys: tuple[str, ...] = ()
    secret_bindings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.allowed_keys)) != len(self.allowed_keys):
            raise ValueError("environment allowlist keys must be unique")
        if len(set(self.secret_bindings)) != len(self.secret_bindings):
            raise ValueError("secret bindings must be unique")
        if any("=" in key or "\x00" in key or not key for key in self.allowed_keys):
            raise ValueError("environment allowlist contains invalid key")
        if any(not binding or "\x00" in binding for binding in self.secret_bindings):
            raise ValueError("secret binding identities must be non-empty")


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    request_id: str
    workload: WorkloadIdentity
    required_capabilities: frozenset[ExecutionCapability] = field(default_factory=frozenset)
    preferred_capabilities: frozenset[ExecutionCapability] = field(default_factory=frozenset)
    isolation_floor: IsolationLevel = IsolationLevel.STATE_ONLY
    persistence: PersistenceMode = PersistenceMode.EPHEMERAL
    immutable_inputs: tuple[ImmutableInputRef, ...] = ()
    outputs: tuple[OutputSpec, ...] = ()
    work_policy: BoundedStatePolicy = field(
        default_factory=lambda: BoundedStatePolicy(0, 0, 0)
    )
    output_policy: BoundedStatePolicy = field(
        default_factory=lambda: BoundedStatePolicy(0, 0, 0)
    )
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    resource_requirements: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    environment_policy: EnvironmentPolicy = field(default_factory=EnvironmentPolicy)
    evidence_requirements: Mapping[str, str] = field(default_factory=dict)
    schema: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != REQUEST_SCHEMA:
            raise ValueError(f"schema must be {REQUEST_SCHEMA}")
        if not self.request_id or not self.request_id.strip():
            raise ValueError("request_id must be non-empty")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        input_ids = [ref.input_id for ref in self.immutable_inputs]
        if len(set(input_ids)) != len(input_ids):
            raise ValueError("immutable input IDs must be unique")
        input_paths = [ref.presentation_path for ref in self.immutable_inputs]
        if len(set(input_paths)) != len(input_paths):
            raise ValueError("immutable input presentation paths must be unique")
        output_ids = [spec.output_id for spec in self.outputs]
        if len(set(output_ids)) != len(output_ids):
            raise ValueError("output IDs must be unique")
        output_paths = [spec.presentation_path for spec in self.outputs]
        if len(set(output_paths)) != len(output_paths):
            raise ValueError("output presentation paths must be unique")
        if set(input_paths).intersection(output_paths):
            raise ValueError("input and output presentation paths must be disjoint")
        if sum(spec.max_bytes for spec in self.outputs) > self.output_policy.max_bytes:
            raise ValueError("declared output byte bounds exceed output_policy.max_bytes")
        if len(self.outputs) > self.output_policy.max_files:
            raise ValueError("declared output count exceeds output_policy.max_files")


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    provider_kind: str
    roles: frozenset[ProviderRole]
    declared_capabilities: frozenset[ExecutionCapability]
    isolation_level: IsolationLevel
    persistence_modes: frozenset[PersistenceMode]
    component_count: int = 1
    priority: int = 0
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_kind:
            raise ValueError("provider_id and provider_kind are required")
        if self.component_count < 1:
            raise ValueError("component_count must be >= 1")
        if (
            ExecutionCapability.STRONG_ISOLATION in self.declared_capabilities
            and self.isolation_level < IsolationLevel.STRONG
        ):
            raise ValueError("strong-isolation capability requires STRONG isolation")


@dataclass(frozen=True, slots=True)
class ProviderProbe:
    provider_id: str
    available: bool
    provider_version: str | None
    verified_capabilities: frozenset[ExecutionCapability]
    isolation_level: IsolationLevel
    persistence_modes: frozenset[PersistenceMode]
    diagnostic_codes: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("provider_id is required")
        if (
            ExecutionCapability.STRONG_ISOLATION in self.verified_capabilities
            and self.isolation_level < IsolationLevel.STRONG
        ):
            raise ValueError(
                "verified strong-isolation capability requires STRONG isolation"
            )


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    provider_id: str
    provider_kind: str
    provider_version: str | None
    provider_runtime_id: str
    roles: frozenset[ProviderRole]
    capabilities: frozenset[ExecutionCapability]
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AppliedNetworkPolicy:
    mode: NetworkMode
    allowed_bindings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is NetworkMode.NONE and self.allowed_bindings:
            raise ValueError("applied network=none cannot contain bindings")
        if self.mode is NetworkMode.BOUNDED and not self.allowed_bindings:
            raise ValueError("applied bounded network requires bindings")
        if len(set(self.allowed_bindings)) != len(self.allowed_bindings):
            raise ValueError("applied network bindings must be unique")


@dataclass(frozen=True, slots=True)
class ExecutionCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@runtime_checkable
class ImmutableInputSource(Protocol):
    """Trusted service-side source for exact input bytes; never serialized."""

    def open_input(self, ref: ImmutableInputRef) -> BinaryIO: ...


@runtime_checkable
class ExecutionPort(Protocol):
    def exec(
        self,
        lease: "ExecutionLease",
        argv: Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionCommandResult: ...


@runtime_checkable
class FixtureTransferPort(Protocol):
    def materialize_inputs(
        self,
        lease: "ExecutionLease",
        inputs: Sequence[ImmutableInputRef],
        source: ImmutableInputSource,
    ) -> tuple[ImmutableInputRef, ...]: ...


@runtime_checkable
class EvidencePort(Protocol):
    def collect(self, lease: "ExecutionLease") -> Mapping[str, object]: ...

    def collect_output(self, lease: "ExecutionLease", output: OutputSpec) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ExecutionPorts:
    execution: ExecutionPort | None = None
    fixture: FixtureTransferPort | None = None
    evidence: EvidencePort | None = None


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    request_id: str
    lease_id: str
    bindings: tuple[ProviderBinding, ...]
    capabilities: frozenset[ExecutionCapability]
    isolation_level: IsolationLevel
    persistence: PersistenceMode
    materialized_inputs: tuple[ImmutableInputRef, ...]
    network_policy: AppliedNetworkPolicy
    work_policy: BoundedStatePolicy
    output_policy: BoundedStatePolicy
    owned_resource_id: str
    ports: ExecutionPorts
    metadata: Mapping[str, str] = field(default_factory=dict)
    schema: str = LEASE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != LEASE_SCHEMA:
            raise ValueError(f"schema must be {LEASE_SCHEMA}")
        if not self.request_id or not self.lease_id or not self.owned_resource_id:
            raise ValueError("request_id, lease_id, and owned_resource_id are required")
        if not self.bindings:
            raise ValueError("execution lease must contain at least one provider binding")
        provider_ids = [binding.provider_id for binding in self.bindings]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("execution lease provider bindings must be unique")


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    request_id: str
    lease_id: str
    execution_id: str
    outcome: ExecutionOutcome
    bindings: tuple[ProviderBinding, ...]
    workload: WorkloadIdentity
    immutable_inputs: tuple[ImmutableInputRef, ...]
    isolation_level: IsolationLevel
    network_policy: AppliedNetworkPolicy
    command: ExecutionCommandResult | None
    cleanup_state: CleanupState
    outputs: tuple[OutputArtifactRef, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    schema: str = RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != RECEIPT_SCHEMA:
            raise ValueError(f"schema must be {RECEIPT_SCHEMA}")
        if not self.request_id or not self.lease_id or not self.execution_id:
            raise ValueError("request_id, lease_id, and execution_id are required")
        if not self.bindings:
            raise ValueError("execution receipt must contain provider bindings")
        output_ids = [output.output_id for output in self.outputs]
        if len(set(output_ids)) != len(output_ids):
            raise ValueError("execution receipt output IDs must be unique")
        if self.outcome is ExecutionOutcome.PASS and self.cleanup_state is not CleanupState.CLEAN:
            raise ValueError("PASS requires clean cleanup")


@runtime_checkable
class ExecutionProvider(Protocol):
    def descriptor(self) -> ProviderDescriptor: ...

    def probe(self) -> ProviderProbe: ...

    def acquire(
        self,
        request: ExecutionRequest,
        *,
        input_source: ImmutableInputSource | None = None,
    ) -> ExecutionLease: ...

    def release(self, lease: ExecutionLease) -> None: ...
