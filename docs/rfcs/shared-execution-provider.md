# RFC: Execution-effect intent boundary

Status: **supersedes the earlier shared execution-provider ownership in this file**

Date: 2026-09-05

Related:

- `DBoz-308/gatekeeper#12`
- `DBoz-308/gatekeeper#13`
- closed `DBoz-308/hermes-agent#4`
- closed `DBoz-308/hermes-agent#5`
- `DBoz-308/hermes-lab#25`
- `DBoz-308/hermes-lab#20`
- `DBoz-308/runner-fleet#26`
- `DBoz-308/media-corpus#86`
- `DBoz-308/media-corpus#88`

## Correction

The earlier version of this RFC incorrectly assigned generic execution-provider ownership to Hermes.

The corrected ecosystem law is:

> **Hermes owns execution/effect intent. Gatekeeper alone owns concrete protected-effect adapters.**

Hermes does not directly own or invoke OpenShell, NemoHermes, Docker/Podman, Incus/KVM, Firecracker/Fireactions, host filesystem materialization, provider credentials, provider acquisition, concrete exec/cancel/destroy operations or provider cleanup/reconciliation.

Those concrete interactions are Gatekeeper effects.

The useful provider-neutral contract work from closed Hermes PRs #4 and #5 remains migration evidence for Gatekeeper. It is not an implementation candidate for Hermes.

## Hermes ownership

Hermes owns:

- reasoning about whether a bounded execution/effect is needed;
- session/delegation context;
- semantic workload intent;
- correlation between an execution request and the Hermes session/task that requested it;
- interpretation of a returned Gatekeeper receipt for Hermes workflow purposes;
- Hermes-specific semantic/runtime-control intent where a Hermes runtime itself is the subject.

Hermes may define or consume a provider-neutral **intent vocabulary** describing requirements such as:

```text
workload identity
required capability class
isolation floor
persistence requirement
content-addressed input identities
bounded output requirements
network requirement
resource ceilings
timeout/deadline
evidence requirements
```

That vocabulary is not a provider API and does not grant machine authority.

## Gatekeeper ownership

Gatekeeper owns the concrete protected-effect boundary, including:

- authority/grant/lease validation;
- surface and execution-profile admission;
- provider discovery that requires protected access;
- provider/controller credential binding;
- host/filesystem materialization;
- concrete provider acquisition;
- concrete exec/cancel/destroy operations;
- process/container/VM/sandbox interaction;
- exact applied isolation/network/resource evidence;
- protected output extraction;
- cleanup/reconciliation and uncertain-outcome handling;
- authoritative Gatekeeper effect receipts.

Execution substrates such as OpenShell or NemoHermes are addressed only through Gatekeeper-owned drivers/adapters.

## No direct-provider law

Hermes must not gain a second privileged-effect path merely because an execution backend has a convenient CLI or SDK.

Normal Hermes agents must not receive:

- Docker sockets;
- OpenShell/provider administration credentials;
- unrestricted sudo;
- arbitrary host paths or mount primitives;
- generic remote shell/SSH credentials;
- provider-admin handles;
- raw device access.

The agent submits a bounded typed Gatekeeper effect request and consumes the bounded receipt/result.

## Gatekeeper receipt relationship

A Gatekeeper execution-effect receipt should be sufficient for Hermes to correlate and interpret the result without exposing provider authority.

Useful evidence may include:

```text
Gatekeeper effect/request identity
caller correlation ref
exact workload/runtime identity
admitted execution profile
provider/backend provenance where safe
applied isolation/network/resource facts
exact content-addressed input/output identities
bounded stdout/stderr/exit evidence
timeout/cancel state
cleanup/reconciliation state
PASS / FAIL / ERROR / UNCERTAIN-style outcome facts
```

Exact Gatekeeper schemas are owned and versioned by Gatekeeper, not Hermes.

Hermes-specific result objects may reference a Gatekeeper receipt; they do not alias Gatekeeper effect IDs or reinterpret uncertain cleanup as success.

## Runner-Fleet relationship

Runner-Fleet owns fleet semantics and desired state:

```text
repository admission
runner requirements
host eligibility/placement
scheduling/routing
desired runner lifecycle
fleet reconciliation meaning
```

It does not become the concrete host-I/O authority. Package installation, systemd/service mutation, protected checkout/runtime-file creation, runner registration and execution-substrate control are Gatekeeper effects under the corrected architecture.

Runner-Fleet may decide that an eligible host must expose a particular admitted Gatekeeper execution capability/profile. Gatekeeper performs the concrete effect; Runner-Fleet consumes the receipt and re-observes fleet state.

## Hermes-Lab relationship

Hermes-Lab owns experiment/scenario/fixture-selection/perturbation/assertion/evaluation semantics.

It may request typed Gatekeeper runtime/filesystem/container/device effects and correlate Gatekeeper receipts into Lab evidence. It does not own the physical execution substrate or provider credential boundary.

Hermes-Lab PR #20 remains valuable proving/migration evidence, especially its descriptor/probe/acquired-state distinction, fail-closed cleanup semantics and strong-provider tests. Concrete provider adapters from that work must migrate behind Gatekeeper rather than into Hermes core.

## Media Corpus MC-20 relationship

Media Corpus owns corpus/artifact/provenance/qualification semantics.

For MC-20 it may submit a bounded isolated-execution intent to Gatekeeper and validate the returned domain output/receipt. Media Corpus must not import or invoke OpenShell, Docker or Gatekeeper driver internals.

The intended path is:

```text
Media Corpus MC-20 qualification semantics
        |
        v
bounded Gatekeeper execution-effect request
        |
        v
Gatekeeper authority/admission + concrete driver
        |
        v
OpenShell / later admitted substrate
        |
        v
Gatekeeper effect receipt + bounded result
        |
        v
Media Corpus validates domain qualification semantics
```

## Wayfinder / DevAtlas boundary

Wayfinder remains planning/task/context state only.

DevAtlas owns development-domain repository/workspace/source/build/CI meaning and exact development-action preconditions. Protected Git/filesystem/provider effects are Gatekeeper operations.

Neither semantic context nor a development capability descriptor authorizes itself.

## Migration evidence from the superseded design

The following ideas from the superseded Hermes provider work remain useful when Gatekeeper defines its execution-effect surface:

- semantic requests name capabilities rather than provider products;
- strong-isolation requirements cannot silently resolve to weaker acquired state;
- content-addressed input/output identity;
- explicit network requirements including deny-all/none;
- bounded resources and output evidence;
- descriptor/preflight claims are not acquired-effect evidence;
- post-effect validation is required where applicable;
- an effectful failure cannot silently fall through to another provider and execute twice;
- cleanup uncertainty fails closed;
- no arbitrary host mount, credential forwarding or generic remote-administration escape hatch.

These are requirements on the Gatekeeper execution-effect/driver design, not a reason to restore a Hermes-owned provider layer.

## Current implementation status

Hermes has no accepted generic execution-provider implementation from this work.

PRs #4 and #5 are closed unmerged. Their branches remain migration/reference evidence only.

Gatekeeper's accepted v0.1 runtime is still read-only. The corrected ownership law does not mean Gatekeeper already implements OpenShell/container execution; that work remains release-gated behind Gatekeeper's own architecture and acceptance process.

## Acceptance law for future Hermes integration

Hermes-side execution integration is acceptable only when:

1. the concrete effect is implemented and admitted through Gatekeeper;
2. Hermes receives no provider/admin/host credential or raw concrete adapter;
3. the Hermes request is bounded and typed rather than a generic privileged shell escape hatch;
4. Gatekeeper receipt identity remains distinct from Hermes session/task identity;
5. failure, timeout, cancellation and uncertain cleanup remain explicit;
6. Hermes can consume the capability without knowing whether Gatekeeper used OpenShell, another local substrate or a governed remote endpoint;
7. changing the concrete provider does not require rewriting Hermes semantic workflow logic.
