from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Mapping, Protocol, Sequence

import yaml

from agent.execution import (
    AppliedNetworkPolicy,
    BoundedStatePolicy,
    ExecutionCapability,
    ExecutionCommandResult,
    ExecutionLease,
    ExecutionPorts,
    ExecutionRequest,
    ImmutableInputSource,
    IsolationLevel,
    NetworkMode,
    OutputSpec,
    PersistenceMode,
    ProviderBinding,
    ProviderDescriptor,
    ProviderProbe,
    ProviderRole,
)


class OpenShellProviderError(RuntimeError):
    pass


class OpenShellCleanupUncertain(OpenShellProviderError):
    """Raised when an owned sandbox cannot be proven absent after cleanup."""


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


class SubprocessCommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(part) for part in argv],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )


@dataclass(frozen=True, slots=True)
class OpenShellProviderConfig:
    cli: str = "openshell"
    sandbox_prefix: str = "hermes-exec"
    operation_timeout_seconds: float = 600.0
    probe_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.cli or not self.cli.strip():
            raise ValueError("OpenShell CLI name must be non-empty")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", self.sandbox_prefix):
            raise ValueError("sandbox_prefix must match [A-Za-z0-9_.-]{1,32}")
        if self.operation_timeout_seconds <= 0:
            raise ValueError("operation_timeout_seconds must be positive")
        if self.probe_timeout_seconds <= 0:
            raise ValueError("probe_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class _ActiveLease:
    sandbox_name: str
    allowed_argv: tuple[str, ...]
    timeout_seconds: float | None


_OCI_DIGEST = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_ZERO_STATE = BoundedStatePolicy(max_bytes=0, max_files=0, max_directories=0)
_PROVIDER_ID = "openshell"
_ROLES = frozenset(
    {
        ProviderRole.ISOLATION,
        ProviderRole.EXECUTION,
        ProviderRole.EVIDENCE,
    }
)
_CAPABILITIES = frozenset(
    {
        ExecutionCapability.EXEC,
        ExecutionCapability.EPHEMERAL,
        ExecutionCapability.STRONG_ISOLATION,
        ExecutionCapability.NETWORK_NONE,
        ExecutionCapability.OWNED_DESTROY,
        ExecutionCapability.PROVIDER_PROVENANCE,
        ExecutionCapability.STDIO_CAPTURE,
    }
)


def _bounded(value: str, limit: int = 8192) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _sandbox_name(prefix: str, request_id: str) -> str:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _policy_document() -> dict[str, object]:
    return {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": [
                "/usr",
                "/lib",
                "/lib64",
                "/bin",
                "/sbin",
                "/etc",
                "/proc",
                "/dev/urandom",
                "/opt",
                "/app",
                "/sandbox",
            ],
            "read_write": ["/dev/null"],
        },
        "landlock": {"compatibility": "hard_requirement"},
        "network_policies": {},
    }


def _policy_digest(document: Mapping[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class OpenShellProvider:
    """Conservative first strong provider over the public OpenShell CLI.

    E1 deliberately does not claim immutable-input or file-output capability.
    Those guarantees require a cleanup-safe pre-policy materialization mechanism
    that OpenShell does not currently expose through its generic upload API.
    """

    def __init__(
        self,
        config: OpenShellProviderConfig | None = None,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config = config or OpenShellProviderConfig()
        self.runner = runner or SubprocessCommandRunner()
        self._active: dict[str, _ActiveLease] = {}

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=_PROVIDER_ID,
            provider_kind="openshell",
            roles=_ROLES,
            declared_capabilities=_CAPABILITIES,
            isolation_level=IsolationLevel.STRONG,
            persistence_modes=frozenset({PersistenceMode.EPHEMERAL}),
            component_count=1,
            priority=100,
            metadata={
                "provider_surface": "openshell-cli",
                "immutable_input": "not-admitted",
                "file_output": "not-admitted",
            },
        )

    def _run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner.run(
                argv,
                timeout=timeout or self.config.operation_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenShellProviderError(
                f"OpenShell controller command timed out: {argv[0]!r}"
            ) from exc
        except OSError as exc:
            raise OpenShellProviderError(
                f"OpenShell controller command could not start: {exc}"
            ) from exc

    def probe(self) -> ProviderProbe:
        if isinstance(self.runner, SubprocessCommandRunner) and shutil.which(self.config.cli) is None:
            return ProviderProbe(
                provider_id=_PROVIDER_ID,
                available=False,
                provider_version=None,
                verified_capabilities=frozenset(),
                isolation_level=IsolationLevel.NONE,
                persistence_modes=frozenset(),
                diagnostic_codes=("openshell_cli_missing",),
                metadata={"probe_scope": "controller_preflight"},
            )

        diagnostics: list[str] = []
        version = self._run(
            [self.config.cli, "--version"],
            timeout=self.config.probe_timeout_seconds,
        )
        status = self._run(
            [self.config.cli, "status"],
            timeout=self.config.probe_timeout_seconds,
        )
        identity = self._run(
            [self.config.cli, "whoami", "--output", "json"],
            timeout=self.config.probe_timeout_seconds,
        )
        if version.returncode != 0:
            diagnostics.append("openshell_version_failed")
        if status.returncode != 0:
            diagnostics.append("openshell_status_failed")
        if identity.returncode != 0:
            diagnostics.append("openshell_identity_failed")
        available = not diagnostics
        version_lines = (version.stdout or version.stderr).strip().splitlines()
        provider_version = version_lines[0][:200] if version_lines else None
        return ProviderProbe(
            provider_id=_PROVIDER_ID,
            available=available,
            provider_version=provider_version,
            verified_capabilities=_CAPABILITIES if available else frozenset(),
            isolation_level=IsolationLevel.STRONG if available else IsolationLevel.NONE,
            persistence_modes=(
                frozenset({PersistenceMode.EPHEMERAL})
                if available
                else frozenset()
            ),
            diagnostic_codes=tuple(diagnostics),
            metadata={"probe_scope": "authenticated_controller_compatibility"},
        )

    def _validate_request(self, request: ExecutionRequest) -> None:
        if request.workload.kind != "container-image":
            raise OpenShellProviderError(
                "OpenShell E1 admits only container-image workloads"
            )
        if not _OCI_DIGEST.fullmatch(request.workload.runtime_identity):
            raise OpenShellProviderError(
                "OpenShell E1 requires an exact OCI image reference with sha256 digest"
            )
        if not request.workload.entrypoint.startswith("/"):
            raise OpenShellProviderError(
                "OpenShell E1 requires an absolute in-image entrypoint"
            )
        if request.persistence is not PersistenceMode.EPHEMERAL:
            raise OpenShellProviderError("OpenShell E1 admits only ephemeral leases")
        if request.isolation_floor > IsolationLevel.STRONG:
            raise OpenShellProviderError("requested isolation exceeds OpenShell E1")
        if request.network_policy.mode is not NetworkMode.NONE:
            raise OpenShellProviderError("OpenShell E1 admits only network=none")
        if request.immutable_inputs:
            raise OpenShellProviderError(
                "OpenShell E1 does not yet admit immutable inputs"
            )
        if request.outputs:
            raise OpenShellProviderError(
                "OpenShell E1 does not yet admit file outputs"
            )
        if request.work_policy != _ZERO_STATE or request.output_policy != _ZERO_STATE:
            raise OpenShellProviderError(
                "OpenShell E1 admits only zero writable work/output state"
            )
        if request.environment_policy.allowed_keys or request.environment_policy.secret_bindings:
            raise OpenShellProviderError(
                "OpenShell E1 does not admit workload environment or secret bindings"
            )
        unknown_resources = set(request.resource_requirements) - {"cpu", "memory"}
        if unknown_resources:
            raise OpenShellProviderError(
                "unsupported OpenShell resource requirements: "
                + ",".join(sorted(unknown_resources))
            )
        unknown_evidence = set(request.evidence_requirements) - {"stdio"}
        if unknown_evidence:
            raise OpenShellProviderError(
                "unsupported OpenShell evidence requirements: "
                + ",".join(sorted(unknown_evidence))
            )
        stdio = request.evidence_requirements.get("stdio")
        if stdio is not None and stdio != "required":
            raise OpenShellProviderError("OpenShell E1 supports stdio=required only")
        if (
            request.timeout_seconds is not None
            and request.timeout_seconds > self.config.operation_timeout_seconds
        ):
            raise OpenShellProviderError(
                "request timeout exceeds OpenShell provider operation timeout"
            )

    def _sandbox_names(self) -> frozenset[str]:
        result = self._run(
            [self.config.cli, "sandbox", "list", "--names"],
            timeout=self.config.probe_timeout_seconds,
        )
        if result.returncode != 0:
            raise OpenShellCleanupUncertain(
                "could not enumerate OpenShell sandboxes for cleanup proof"
            )
        return frozenset(line.strip() for line in result.stdout.splitlines() if line.strip())

    def _destroy_name(self, name: str) -> None:
        deleted = self._run([self.config.cli, "sandbox", "delete", name])
        if deleted.returncode != 0:
            raise OpenShellCleanupUncertain(
                f"OpenShell sandbox deletion was not confirmed for {name!r}"
            )
        if name in self._sandbox_names():
            raise OpenShellCleanupUncertain(
                f"OpenShell sandbox still exists after deletion: {name!r}"
            )

    def _cleanup_if_present(self, name: str) -> None:
        names = self._sandbox_names()
        if name in names:
            self._destroy_name(name)

    def _verify_active_policy(self, name: str, requested: Mapping[str, object]) -> None:
        result = self._run(
            [self.config.cli, "sandbox", "get", name, "--policy-only"],
            timeout=self.config.probe_timeout_seconds,
        )
        if result.returncode != 0:
            raise OpenShellProviderError("failed to read acquired OpenShell policy")
        try:
            active = yaml.safe_load(result.stdout)
        except yaml.YAMLError as exc:
            raise OpenShellProviderError("acquired OpenShell policy is not valid YAML") from exc
        if not isinstance(active, dict):
            raise OpenShellProviderError("acquired OpenShell policy root is not a mapping")
        for key in ("filesystem_policy", "landlock", "network_policies"):
            if active.get(key) != requested.get(key):
                raise OpenShellProviderError(
                    f"acquired OpenShell policy does not preserve requested {key}"
                )

    def acquire(
        self,
        request: ExecutionRequest,
        *,
        input_source: ImmutableInputSource | None = None,
    ) -> ExecutionLease:
        del input_source
        self._validate_request(request)
        probe = self.probe()
        if not probe.available:
            raise OpenShellProviderError(
                "OpenShell provider unavailable: " + ",".join(probe.diagnostic_codes)
            )

        name = _sandbox_name(self.config.sandbox_prefix, request.request_id)
        if name in self._sandbox_names():
            raise OpenShellProviderError(
                f"OpenShell sandbox name collision for request {request.request_id!r}"
            )

        policy = _policy_document()
        with tempfile.TemporaryDirectory(prefix="hermes-openshell-e1-") as directory:
            policy_path = Path(directory) / "policy.yaml"
            policy_path.write_text(
                yaml.safe_dump(policy, sort_keys=True),
                encoding="utf-8",
            )
            argv = [
                self.config.cli,
                "sandbox",
                "create",
                "--name",
                name,
                "--from",
                request.workload.runtime_identity,
                "--policy",
                str(policy_path),
                "--no-auto-providers",
                "--no-tty",
                "--label",
                "hermes-execution=true",
                "--label",
                "hermes-execution-request="
                + hashlib.sha256(request.request_id.encode("utf-8")).hexdigest()[:20],
            ]
            cpu = request.resource_requirements.get("cpu")
            memory = request.resource_requirements.get("memory")
            if cpu:
                argv.extend(["--cpu", cpu])
            if memory:
                argv.extend(["--memory", memory])
            argv.extend(["--", "/bin/true"])
            created = self._run(argv)

        if created.returncode != 0:
            try:
                self._cleanup_if_present(name)
            except OpenShellCleanupUncertain as cleanup_exc:
                raise OpenShellCleanupUncertain(
                    "OpenShell creation failed and cleanup could not be proven"
                ) from cleanup_exc
            raise OpenShellProviderError(
                "OpenShell sandbox creation failed "
                f"(exit={created.returncode}, stderr={_bounded(created.stderr)!r})"
            )

        try:
            ready = self._run(
                [self.config.cli, "sandbox", "get", name],
                timeout=self.config.probe_timeout_seconds,
            )
            if ready.returncode != 0:
                raise OpenShellProviderError("OpenShell sandbox failed readiness inspection")
            self._verify_active_policy(name, policy)
        except BaseException:
            try:
                self._destroy_name(name)
            except OpenShellCleanupUncertain as cleanup_exc:
                raise OpenShellCleanupUncertain(
                    "OpenShell post-acquisition validation failed and cleanup was uncertain"
                ) from cleanup_exc
            raise

        binding = ProviderBinding(
            provider_id=_PROVIDER_ID,
            provider_kind="openshell",
            provider_version=probe.provider_version,
            provider_runtime_id=name,
            roles=_ROLES,
            capabilities=_CAPABILITIES,
            metadata={
                "sandbox_name": name,
                "runtime_identity": request.workload.runtime_identity,
                "policy_digest": _policy_digest(policy),
                "policy_attestation": "post-acquire-active-policy-checked",
                "network_attestation": "empty-network-policy-default-deny",
                "provider_auto_attachment": "disabled",
                "writable_work_state": "none",
            },
        )
        lease = ExecutionLease(
            request_id=request.request_id,
            lease_id=f"openshell:{name}",
            bindings=(binding,),
            capabilities=_CAPABILITIES,
            isolation_level=IsolationLevel.STRONG,
            persistence=PersistenceMode.EPHEMERAL,
            materialized_inputs=(),
            network_policy=AppliedNetworkPolicy(mode=NetworkMode.NONE),
            work_policy=request.work_policy,
            output_policy=request.output_policy,
            owned_resource_id=f"openshell:sandbox:{name}",
            ports=ExecutionPorts(execution=self, evidence=self),
            metadata={
                "provider_attestation": "post_acquire",
                "policy_digest": _policy_digest(policy),
            },
        )
        self._active[lease.lease_id] = _ActiveLease(
            sandbox_name=name,
            allowed_argv=(request.workload.entrypoint, *request.workload.argv),
            timeout_seconds=request.timeout_seconds,
        )
        return lease

    def _active_lease(self, lease: ExecutionLease) -> _ActiveLease:
        active = self._active.get(lease.lease_id)
        if active is None:
            raise OpenShellProviderError("OpenShell lease is not active in this provider")
        binding_names = {
            binding.provider_runtime_id
            for binding in lease.bindings
            if binding.provider_id == _PROVIDER_ID
        }
        if binding_names != {active.sandbox_name}:
            raise OpenShellProviderError("OpenShell lease provider binding mismatch")
        return active

    def exec(
        self,
        lease: ExecutionLease,
        argv: Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionCommandResult:
        active = self._active_lease(lease)
        actual_argv = tuple(str(part) for part in argv)
        if actual_argv != active.allowed_argv:
            raise OpenShellProviderError(
                "OpenShell E1 execution is restricted to the admitted request workload"
            )
        effective_timeout = timeout_seconds
        if active.timeout_seconds is not None:
            if effective_timeout is None:
                effective_timeout = active.timeout_seconds
            else:
                effective_timeout = min(effective_timeout, active.timeout_seconds)
        if (
            effective_timeout is not None
            and effective_timeout > self.config.operation_timeout_seconds
        ):
            effective_timeout = self.config.operation_timeout_seconds

        command = [
            self.config.cli,
            "sandbox",
            "exec",
            "--name",
            active.sandbox_name,
            "--workdir",
            "/sandbox",
            "--no-tty",
        ]
        if effective_timeout is not None:
            command.extend(["--timeout", str(effective_timeout)])
        command.extend(["--", *actual_argv])
        result = self._run(command, timeout=effective_timeout)
        return ExecutionCommandResult(
            argv=actual_argv,
            returncode=result.returncode,
            stdout=_bounded(result.stdout or ""),
            stderr=_bounded(result.stderr or ""),
        )

    def collect(self, lease: ExecutionLease) -> Mapping[str, object]:
        active = self._active_lease(lease)
        result = self._run(
            [self.config.cli, "sandbox", "get", active.sandbox_name],
            timeout=self.config.probe_timeout_seconds,
        )
        return {
            "provider": _PROVIDER_ID,
            "sandbox": active.sandbox_name,
            "ready": result.returncode == 0,
            "status_tail": _bounded(result.stdout or result.stderr, 1200),
        }

    def collect_output(self, lease: ExecutionLease, output: OutputSpec) -> bytes:
        del lease, output
        raise OpenShellProviderError(
            "OpenShell E1 does not admit file-output collection"
        )

    def release(self, lease: ExecutionLease) -> None:
        active = self._active_lease(lease)
        self._destroy_name(active.sandbox_name)
        self._active.pop(lease.lease_id, None)
