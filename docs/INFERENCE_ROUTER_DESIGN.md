# Proposed first-class inference-router plugin contract

Status: **proposed design; not implemented by this document**.

This document defines the Hermes-side extension point required by external inference-routing policy systems such as Switchyard.

## Problem

Hermes model-provider plugins represent **inference backends**. They own provider metadata used by Hermes to resolve credentials, API mode, endpoint behavior and model catalogs.

An inference router is different: it selects among provider/model/MoA targets that Hermes already knows how to execute.

Registering a router as a fake model provider would force it to proxy execution and duplicate Hermes provider clients, credentials, retries, fallbacks, prompt/runtime state and accounting.

Existing `llm_request` middleware is too late for clean cross-provider selection because provider/runtime resolution has already occurred. `llm_execution` can replace execution, but using it as cross-provider routing would recreate the provider runtime outside Hermes.

## Decision shape

Hermes should gain a first-class native plugin capability tentatively named **inference-router**.

Conceptual registration:

~~~python
ctx.register_inference_router(
    name="switchyard",
    resolver=resolve_route,
    observer=observe_route_outcome,
)
~~~

Exact method/type names are implementation details. The semantic contract is the architectural requirement.

## Configuration

Routing is orthogonal to the normal model/provider default:

~~~yaml
model:
  provider: anthropic
  model: claude-sonnet-5

routing:
  resolver: switchyard
  mode: active
  policy: balanced
~~~

When no router is installed/enabled, Hermes behaves exactly as it does today.

The provider/model picker must not list an inference router as though it were a provider endpoint.

## Logical route boundary

Hermes should establish one shared logical route representation before provider/runtime/client construction.

~~~text
explicit session/user/task pin
        +
configured default model/provider
        +
safe route/task/capability metadata
        |
        v
LogicalRouteContext
        |
        v
one enabled inference-router resolver
        |
        v
LogicalRouteSelection
 keep | select | deny
        |
        v
Hermes validates provider/model/MoA target and model guards
        |
        v
existing runtime_provider / credential / client construction
        |
        v
existing Hermes execution
~~~

The router never receives raw credentials.

## Resolver input

Safe, additive fields may include:

- route/session/turn/lane identity;
- configured/default provider and model;
- explicit pin plus provenance;
- capability/modalities/context requirements;
- reasoning-effort/fallback defaults;
- safe task metadata supplied by extensions such as Wayfinder;
- trace context;
- immutable executable-inventory reference.

Payloads should follow Hermes' additive plugin compatibility rules.

## Resolver output

A resolver may return:

- action: keep, select, deny;
- provider/model or approved named MoA preset;
- reasoning effort;
- bounded approved fallback chain;
- router provenance such as decision ID, target ID, policy version and model-atlas version.

Hermes validates all selections through its existing provider/model/MoA catalogs and cost/data guards before runtime construction.

## Route lifetime

Routing applies to an explicit **route scope/epoch**, not every HTTP request.

A selected model/provider must remain stable through a tool loop unless normal Hermes failure/fallback semantics require a pre-approved transition.

This protects prompt-cache/runtime state and makes traces replayable.

## Observer/correlation fields

Hermes should propagate additive correlation metadata through pre/post/error observations and usage traces where available:

- distributed trace context;
- route decision ID;
- selected target ID;
- policy/Atlas version;
- actual provider/model;
- usage/cost/timing fields already known by Hermes.

The router observes and normalizes this telemetry; it does not become execution authority.

## Plugins, tools and skills

A standalone router product can ship as a normal Hermes plugin package containing the router registration plus tools/commands/skills.

Model-visible tools should be read-only inspection/explanation surfaces. They must not be the mechanism by which the model arbitrarily chooses its own provider or rewrites routing policy.

## Compatibility and rollout

Recommended implementation order:

1. refactor CLI/Gateway logical default/pin selection into one shared structure with parity tests;
2. add the inference-router registration surface disabled by default;
3. validate resolver outputs using existing provider/model guards;
4. propagate route provenance;
5. prove disabled behavior parity and tool-loop stickiness;
6. enable external routers in shadow mode first;
7. admit active routing only after integration tests prove provider/runtime semantics remain owned by Hermes.

This design intentionally does not change provider plugins, credential pools, MoA execution, auxiliary execution or the provider transport path.
