# RFC: Shared execution-provider substrate

Status: proposed

Date: 2026-09-05

Related:

- `DBoz-308/hermes-lab#25`
- `DBoz-308/hermes-lab#20`
- `DBoz-308/runner-fleet#26`
- `DBoz-308/media-corpus#86`
- `DBoz-308/media-corpus#88`

## Problem

Hermes-Lab has developed a capable provider-neutral runtime abstraction while proving strong isolated Hermes execution. A second concrete consumer now exists: Media Corpus MC-20 needs to execute a pinned Essentia qualification workload through strong isolation without granting its ordinary GitHub runner Docker-socket, root, provider-admin, or generic host-execution authority.

That makes the generic execution semantics a shared Hermes substrate rather than a Hermes-Lab implementation detail or Runner-Fleet execution-cell responsibility.

The shared substrate must be independently consumable by Hermes, Hermes-Lab, CI integrations, and future adapters. It must not require a non-Hermes workload to import Hermes-Lab scenario semantics.

## Decision

Hermes defines a stable provider-neutral **execution substrate contract** with replaceable backend implementations.

The initial implementation may live inside the Hermes codebase as a bounded package/module/service surface. A new standalone repository is not required until packaging, privilege separation, deployment, or dependency pressure demonstrates that it is useful.

Hermes-Lab PR #20 is migration input, not the shared API itself.

## Ownership

The shared execution substrate owns:

- execution request, provider, lease, lifecycle, and receipt contracts;
- capability vocabulary and isolation floors;
- immutable input/materialization requirements;
- bounded writable work/output state;
- network policy requirements;
- resource and timeout requirements;
- provider selection among explicitly configured providers;
- acquired-lease validation;
- timeout, cancel, release, destroy, and uncertain-cleanup semantics;
- provider/profile/backend/runtime provenance;
- stdout, stderr, exit status, and bounded output evidence;
- replaceable adapters for NemoHermes/OpenShell, Incus/KVM, Firecracker/Fireactions, container substrates, and future providers.

It does not own:

- Hermes-Lab scenario definitions, perturbations, assertions, scoring, or experiment evidence;
- Runner-Fleet physical-host placement, repository admission, runner lifecycle, or CI transport authority;
- Wayfinder planning/task/context state;
- DevAtlas development-estate authority;
- Gatekeeper authorization policy;
- application product acceptance semantics;
- generic remote-machine administration.

## Migration from Hermes-Lab PR #20

The following PR #20 concepts are retained:

- provider descriptor, current provider probe, and acquired lease are distinct evidence classes;
- semantic requirements name capabilities, not provider products;
- strong-isolation requirements cannot resolve to weaker leases;
- persistence is explicit;
- providers may implement multiple orthogonal capability ports;
- arbitrary incomplete providers are never silently unioned;
- explicit composite providers may exist behind one provider boundary;
- acquired runtime facts are validated after effectful acquisition;
- an acquisition that fails post-acquisition validation does not silently fall through to another provider;
- cleanup uncertainty fails closed.

The following PR #20 concepts remain Lab/Hermes-specific and are not copied into the generic core:

- `RuntimeRequirements.hermes_artifact`;
- Hermes control RPC, gateway restart, session semantics, and Hermes-specific capability names;
- `hermes_lab.runtime_lease.v1`;
- `hermes_lab.run_receipt.v2`;
- Lab scenario assertions and fault semantics.

Hermes control/runtime behavior becomes an extension layered over the generic execution lease.

## Generic execution request v1

A request identifies one bounded workload and the guarantees required to execute it.

Minimum semantic fields:

```text
schema = hermes.execution.request.v1
request_id
workload
required_capabilities
preferred_capabilities
isolation_floor
persistence
immutable_inputs
work_policy
output_policy
network_policy
resource_requirements
timeout/deadline
environment_policy
secret_bindings
evidence_requirements
```

### Workload

The workload is typed and bounded. It records enough exact identity to prove what was requested, for example:

```text
kind
runtime/image/artifact identity
entrypoint
argv
working-directory class
```

The shared API is not an unrestricted host shell. Provider implementations may support bounded command execution inside provider-owned leases, but requests cannot select arbitrary host namespaces, arbitrary host mounts, arbitrary remote destinations, or provider-admin operations.

### Immutable inputs

Each input has stable identity and intended presentation semantics, for example:

```text
input_id
digest / exact artifact identity
media/type metadata where needed
presentation path inside the lease
read_only = true
```

The source path on the physical host is not semantic identity and must not become a generic caller-selected host mount.

### Work/output policy

Requests bound writable state by provider-owned classes rather than arbitrary host paths. Bounds may include bytes, file count, directory count, runtime duration, and output allowlists.

### Network policy

Network is deny-by-default or explicitly bounded. `none` is a first-class policy. Service bindings are explicit capabilities rather than implicit LAN access.

### Environment and secrets

Only allowlisted environment fields enter the workload. Secret values are never embedded in request schemas or receipts; only opaque secret-binding identities may appear. Provider/controller credentials never enter the workload context.

## Provider evidence model

### ProviderDescriptor

Static potential capability and compatibility metadata. It is not proof that the provider is currently usable.

### ProviderProbe

Current provider/controller availability evidence. It is still not proof of one acquired execution context.

### ExecutionLease

Facts about one acquired execution context.

The lease must include at least:

```text
schema = hermes.execution.lease.v1
request_id
lease_id
provider bindings
actually acquired capabilities
isolation level
persistence mode
materialized immutable inputs
network policy applied
work/output bounds
owned-resource identity
available ports
metadata/provenance
```

A provider label, Runner-Fleet label, descriptor, or preflight probe may narrow candidates but cannot substitute for acquired-lease evidence.

## Capability ports

Prefer narrow ports over one provider god-object.

Initial generic ports:

```text
ExecutionLifecyclePort
FixtureTransferPort
EvidencePort
```

Optional ports admitted only when a concrete provider/consumer needs them:

```text
SnapshotPort
ServiceBindingPort
InteractiveExecutionPort
```

Hermes-specific extensions may add:

```text
HermesRuntimePort
HermesControlPort
HermesLifecyclePort
```

One provider object may implement several ports. Explicit composite providers may delegate ports only across reviewed compatible boundaries while presenting one validated lease.

## Resolution law

Resolution follows these steps:

```text
request
 -> inspect explicitly configured provider descriptors
 -> probe candidates
 -> choose one eligible provider or one explicit composite provider
 -> acquire once
 -> validate acquired lease against request
 -> execute through lease ports
 -> collect bounded evidence
 -> release/destroy owned resources
 -> issue receipt
```

After effectful acquisition, validation failure does not cause automatic fallback to another provider. The first provider is released/destroyed; cleanup ambiguity produces an error/uncertain outcome.

Automatic selection may prefer fewer composed components and preferred capabilities, but it cannot weaken a required capability or isolation floor.

## Generic execution receipt v1

Every attempted effectful execution should produce bounded evidence when possible.

Minimum receipt fields:

```text
schema = hermes.execution.receipt.v1
request_id
lease_id
execution_id
outcome
provider/profile/backend/runtime provenance
exact workload identity
exact immutable input identities
isolation facts
network facts
timing / timeout / cancel state
stdout/stderr/exit evidence
output identities
cleanup state
degraded/diagnostic codes
```

Outcome must distinguish at least successful execution, workload failure, infrastructure/error failure, and uncertain state. Exact enum naming may be finalized in schema work, but cleanup ambiguity cannot be represented as clean success.

Large output may be represented by bounded content-addressed references rather than unbounded inline text.

## Security invariants

The generic provider layer must not create a second remote-administration channel.

Mandatory invariants:

- no raw Docker socket to ordinary callers or workload contexts;
- no unrestricted sudo;
- no arbitrary host-path mount primitive;
- no inherited operator home, browser/session state, SSH/GPG keys, or ambient environment;
- no provider credential returned to callers or injected into workload state;
- no generic SSH, host shell, arbitrary file-copy, port-forward, TCP proxy, or caller-selected network destination surface;
- immutable inputs are exact and post-acquisition attested;
- writable resources are provider-owned and bounded;
- network access is denied or explicitly admitted;
- destroy/release operations can affect only lease-owned resources;
- timeout/cancel/interruption/cleanup uncertainty is explicit;
- privileged provider administration is separately authorized and is not implied by execution access.

## Runner-Fleet relationship

Runner-Fleet remains responsible for physical execution-fleet and CI state:

```text
repository admission
runner provisioning/lifecycle
host capability observation
host placement/routing
CI transport/provider integration
```

Runner-Fleet may verify that the shared provider client/profile is usable on a host and may expose that as verified eligibility. It may invoke a bounded client and collect its receipt for CI. It does not define isolation semantics or become the executor.

Host placement and execution-provider/profile/runtime selection remain separate decisions.

## Hermes-Lab relationship

Hermes-Lab owns experiments. It consumes the shared execution substrate to obtain a validated lease and then applies Lab-owned fixture selection, perturbation, assertions, scenario evidence, comparison, and evaluation semantics.

A Lab receipt may embed/reference a generic execution lease/receipt, but the generic receipt does not gain Lab scenario fields.

## Hermes relationship

Normal Hermes agent execution can use the generic substrate directly for bounded isolated jobs. Hermes-specific runtime/control ports extend the generic lease where a workload is itself a Hermes runtime.

Execution access does not imply Runner-Fleet administration or Gatekeeper bypass.

## Media Corpus MC-20 proving consumer

The first independent non-Hermes proving consumer is Media Corpus MC-20.

MC-20 must be able to request execution of the exact pinned Essentia workload and validate evidence for:

- exact request/lease/receipt linkage;
- exact provider/profile/backend identity;
- pinned Essentia runtime/image digest;
- `network=none` equivalent;
- exact immutable input presented read-only;
- bounded writable work/output state;
- no host Docker socket;
- no unrestricted sudo or provider credential;
- complete stdout/stderr/exit evidence;
- bounded failed/interrupted/uncertain evidence;
- cleanup ownership and result;
- fail-closed behavior when required provider capability is unavailable.

Media Corpus must not implement the generic provider itself.

## Delivery slices

### E0 — contract freeze

- generic request/lease/receipt schemas;
- capability and isolation vocabulary;
- generic ports;
- resolver/acquired-lease validation law;
- fake provider tests.

### E1 — real strong provider

- adapt/bridge the already-developed NemoHermes/OpenShell path or another admitted strong provider;
- prove exact immutable input, bounded network, execution evidence, and owned cleanup;
- no Hermes-specific requirement in the generic path.

### E2 — CI consumer

- bounded unprivileged CLI/API client;
- Media Corpus MC-20 consumer proof;
- Runner-Fleet capability/eligibility integration without execution ownership.

### E3 — Hermes-Lab migration

- Lab consumes shared generic substrate;
- Lab-only scenario and receipt semantics remain local;
- exact strong-provider acceptance continues to prove the real path.

### E4 — Hermes-native extension

- Hermes runtime/control ports layer over generic leases;
- agents can request bounded isolated jobs without gaining provider-admin or fleet-admin authority.

## Acceptance

The shared substrate is not accepted until:

1. a non-Hermes fake workload executes without importing Hermes-Lab;
2. strong-isolation requirements cannot resolve to a weaker acquired lease;
3. exact immutable input attestation is checked after acquisition;
4. provider descriptor/probe claims cannot substitute for lease evidence;
5. effectful acquisition failure does not silently fall through to another provider;
6. timeout/cancel/cleanup uncertainty is receipt-backed;
7. ordinary callers have no Docker socket, unrestricted sudo, provider credential, arbitrary host mount, or generic remote-admin channel;
8. MC-20 real qualification passes through the generic contract;
9. Hermes-Lab consumes the same substrate without surrendering scenario/assertion ownership;
10. Runner-Fleet can route/verify capability without becoming the generic executor;
11. adding another provider implementation does not change consumer semantic schemas.
