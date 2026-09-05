from __future__ import annotations

from dataclasses import replace
import subprocess

import pytest
import yaml

from agent.execution import (
    BoundedStatePolicy,
    ExecutionCapability,
    ExecutionRequest,
    ExecutionResolutionError,
    IsolationLevel,
    NetworkMode,
    NetworkPolicy,
    PersistenceMode,
    WorkloadIdentity,
    acquire_validated_execution,
    resolve_execution_provider,
)
from agent.execution.providers.openshell import (
    OpenShellCleanupUncertain,
    OpenShellProvider,
    OpenShellProviderError,
)


ZERO = BoundedStatePolicy(max_bytes=0, max_files=0, max_directories=0)
REQUIRED = frozenset(
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
IMAGE = "registry.example/tool@sha256:" + "a" * 64


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=list(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class _Runner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.sandboxes: set[str] = set()
        self.policies: dict[str, dict[str, object]] = {}
        self.policy_override: dict[str, object] | None = None
        self.create_returncode = 0
        self.delete_returncode = 0
        self.delete_effective = True
        self.status_returncode = 0
        self.identity_returncode = 0
        self.exec_returncode = 0
        self.exec_stdout = "ok\n"
        self.exec_stderr = ""

    def run(self, argv, *, timeout=None):
        del timeout
        command = tuple(str(part) for part in argv)
        self.commands.append(command)
        if command == ("openshell", "--version"):
            return _completed(command, stdout="openshell 0.test\n")
        if command == ("openshell", "status"):
            return _completed(command, returncode=self.status_returncode, stdout="ready\n")
        if command == ("openshell", "whoami", "--output", "json"):
            return _completed(
                command,
                returncode=self.identity_returncode,
                stdout='{"subject":"test"}\n',
            )
        if command == ("openshell", "sandbox", "list", "--names"):
            return _completed(command, stdout="".join(f"{name}\n" for name in sorted(self.sandboxes)))
        if command[:3] == ("openshell", "sandbox", "create"):
            name = command[command.index("--name") + 1]
            policy_path = command[command.index("--policy") + 1]
            with open(policy_path, encoding="utf-8") as stream:
                self.policies[name] = yaml.safe_load(stream)
            if self.create_returncode == 0:
                self.sandboxes.add(name)
            return _completed(
                command,
                returncode=self.create_returncode,
                stderr="create failed" if self.create_returncode else "",
            )
        if command[:3] == ("openshell", "sandbox", "get"):
            name = command[3]
            if name not in self.sandboxes:
                return _completed(command, returncode=1, stderr="not found")
            if command[-1] == "--policy-only":
                document = self.policy_override or self.policies[name]
                return _completed(command, stdout=yaml.safe_dump(document, sort_keys=True))
            return _completed(command, stdout=f"sandbox {name} ready\n")
        if command[:3] == ("openshell", "sandbox", "exec"):
            return _completed(
                command,
                returncode=self.exec_returncode,
                stdout=self.exec_stdout,
                stderr=self.exec_stderr,
            )
        if command[:3] == ("openshell", "sandbox", "delete"):
            name = command[3]
            if self.delete_returncode == 0 and self.delete_effective:
                self.sandboxes.discard(name)
            return _completed(command, returncode=self.delete_returncode)
        raise AssertionError(f"unexpected OpenShell command: {command!r}")


def _request(**changes) -> ExecutionRequest:
    request = ExecutionRequest(
        request_id="request-1",
        workload=WorkloadIdentity(
            kind="container-image",
            runtime_identity=IMAGE,
            entrypoint="/bin/echo",
            argv=("hello",),
        ),
        required_capabilities=REQUIRED,
        isolation_floor=IsolationLevel.STRONG,
        persistence=PersistenceMode.EPHEMERAL,
        work_policy=ZERO,
        output_policy=ZERO,
        network_policy=NetworkPolicy(mode=NetworkMode.NONE),
        timeout_seconds=10.0,
        evidence_requirements={"stdio": "required"},
    )
    return replace(request, **changes)


def _acquire(runner: _Runner):
    provider = OpenShellProvider(runner=runner)
    request = _request()
    resolved = resolve_execution_provider(request, [provider])
    lease = acquire_validated_execution(request, resolved)
    return provider, request, lease


def test_descriptor_withholds_unproven_input_output_and_bounded_network_capabilities() -> None:
    descriptor = OpenShellProvider(runner=_Runner()).descriptor()
    assert ExecutionCapability.STRONG_ISOLATION in descriptor.declared_capabilities
    assert ExecutionCapability.NETWORK_NONE in descriptor.declared_capabilities
    assert ExecutionCapability.IMMUTABLE_INPUT not in descriptor.declared_capabilities
    assert ExecutionCapability.OUTPUT_CAPTURE not in descriptor.declared_capabilities
    assert ExecutionCapability.BOUNDED_NETWORK not in descriptor.declared_capabilities


def test_probe_requires_authenticated_controller() -> None:
    runner = _Runner()
    runner.identity_returncode = 1
    probe = OpenShellProvider(runner=runner).probe()
    assert probe.available is False
    assert probe.isolation_level is IsolationLevel.NONE
    assert "openshell_identity_failed" in probe.diagnostic_codes


def test_resolver_rejects_immutable_input_request_before_acquisition() -> None:
    provider = OpenShellProvider(runner=_Runner())
    request = replace(
        _request(),
        immutable_inputs=(),
        required_capabilities=REQUIRED | {ExecutionCapability.IMMUTABLE_INPUT},
    )
    with pytest.raises(ExecutionResolutionError, match="no configured execution provider"):
        resolve_execution_provider(request, [provider])


def test_provider_requires_exact_digest_pinned_image() -> None:
    runner = _Runner()
    provider = OpenShellProvider(runner=runner)
    request = replace(
        _request(),
        workload=replace(_request().workload, runtime_identity="registry.example/tool:latest"),
    )
    with pytest.raises(OpenShellProviderError, match="exact OCI image"):
        provider.acquire(request)
    assert not any(command[:3] == ("openshell", "sandbox", "create") for command in runner.commands)


def test_acquire_uses_no_providers_no_tty_exact_image_and_strict_empty_network_policy() -> None:
    runner = _Runner()
    provider, request, lease = _acquire(runner)

    create = next(
        command
        for command in runner.commands
        if command[:3] == ("openshell", "sandbox", "create")
    )
    assert "--no-auto-providers" in create
    assert "--no-tty" in create
    assert "--provider" not in create
    assert create[create.index("--from") + 1] == IMAGE
    assert create[-2:] == ("--", "/bin/true")

    sandbox_name = lease.bindings[0].provider_runtime_id
    policy = runner.policies[sandbox_name]
    assert policy["network_policies"] == {}
    assert policy["landlock"] == {"compatibility": "hard_requirement"}
    assert policy["filesystem_policy"]["include_workdir"] is False
    assert policy["filesystem_policy"]["read_write"] == ["/dev/null"]

    assert lease.isolation_level is IsolationLevel.STRONG
    assert lease.network_policy.mode is NetworkMode.NONE
    assert lease.work_policy == ZERO
    assert lease.output_policy == ZERO
    assert lease.materialized_inputs == ()
    assert lease.ports.execution is provider
    assert lease.ports.evidence is provider
    assert lease.request_id == request.request_id


def test_exec_is_restricted_to_exact_admitted_workload() -> None:
    runner = _Runner()
    provider, request, lease = _acquire(runner)
    execution = lease.ports.execution
    assert execution is not None

    with pytest.raises(OpenShellProviderError, match="restricted to the admitted request workload"):
        execution.exec(lease, ("/bin/sh", "-c", "id"))

    result = execution.exec(
        lease,
        (request.workload.entrypoint, *request.workload.argv),
        timeout_seconds=30.0,
    )
    assert result.returncode == 0
    assert result.stdout == "ok\n"
    command = runner.commands[-1]
    assert command[:3] == ("openshell", "sandbox", "exec")
    assert command[-3:] == ("--", "/bin/echo", "hello")
    assert command[command.index("--timeout") + 1] == "10.0"


def test_release_deletes_owned_sandbox_and_confirms_absence() -> None:
    runner = _Runner()
    provider, _request_value, lease = _acquire(runner)
    sandbox_name = lease.bindings[0].provider_runtime_id
    assert sandbox_name in runner.sandboxes
    provider.release(lease)
    assert sandbox_name not in runner.sandboxes
    with pytest.raises(OpenShellProviderError, match="not active"):
        provider.collect(lease)


def test_post_acquire_policy_mismatch_fails_and_cleans_up() -> None:
    runner = _Runner()
    runner.policy_override = {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": ["/usr"],
            "read_write": ["/dev/null", "/tmp"],
        },
        "landlock": {"compatibility": "hard_requirement"},
        "network_policies": {},
    }
    provider = OpenShellProvider(runner=runner)
    with pytest.raises(OpenShellProviderError, match="filesystem_policy"):
        provider.acquire(_request())
    assert runner.sandboxes == set()


def test_cleanup_uncertainty_overrides_post_acquire_failure() -> None:
    runner = _Runner()
    runner.policy_override = {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": ["/usr"],
            "read_write": ["/tmp"],
        },
        "landlock": {"compatibility": "hard_requirement"},
        "network_policies": {},
    }
    runner.delete_effective = False
    provider = OpenShellProvider(runner=runner)
    with pytest.raises(OpenShellCleanupUncertain, match="cleanup was uncertain"):
        provider.acquire(_request())


def test_provider_rejects_writable_state_outputs_secrets_and_non_none_network() -> None:
    provider = OpenShellProvider(runner=_Runner())
    with pytest.raises(OpenShellProviderError, match="zero writable"):
        provider.acquire(
            replace(
                _request(),
                work_policy=BoundedStatePolicy(1, 1, 1),
            )
        )
    with pytest.raises(OpenShellProviderError, match="network=none"):
        provider.acquire(
            replace(
                _request(),
                network_policy=NetworkPolicy(
                    mode=NetworkMode.BOUNDED,
                    allowed_bindings=("example",),
                ),
            )
        )
