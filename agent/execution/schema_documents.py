from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (
    CleanupState,
    ExecutionCapability,
    ExecutionOutcome,
    LEASE_SCHEMA,
    NetworkMode,
    PersistenceMode,
    ProviderRole,
    RECEIPT_SCHEMA,
    REQUEST_SCHEMA,
)

_JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
_SHA256_PATTERN = "^sha256:[0-9a-f]{64}$"
_RELATIVE_PATH_PATTERN = "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))(?!.*(?:^|/)\\.(?:/|$)).+$"


def _string_map() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }


def _string_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "uniqueItems": True}


def _capabilities() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"enum": [item.value for item in ExecutionCapability]},
        "uniqueItems": True,
    }


def _workload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "runtime_identity",
            "entrypoint",
            "argv",
            "working_directory_class",
        ],
        "properties": {
            "kind": {"type": "string", "minLength": 1},
            "runtime_identity": {"type": "string", "minLength": 1},
            "entrypoint": {"type": "string", "minLength": 1},
            "argv": {"type": "array", "items": {"type": "string"}},
            "working_directory_class": {"type": "string", "minLength": 1},
        },
    }


def _immutable_input() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "input_id",
            "digest",
            "size_bytes",
            "presentation_path",
            "read_only",
        ],
        "properties": {
            "input_id": {"type": "string", "minLength": 1},
            "digest": {"type": "string", "pattern": _SHA256_PATTERN},
            "size_bytes": {"type": "integer", "minimum": 0},
            "presentation_path": {
                "type": "string",
                "minLength": 1,
                "pattern": _RELATIVE_PATH_PATTERN,
            },
            "media_type": {"type": "string", "minLength": 1},
            "read_only": {"const": True},
        },
    }


def _output_spec() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["output_id", "presentation_path", "max_bytes", "required"],
        "properties": {
            "output_id": {"type": "string", "minLength": 1},
            "presentation_path": {
                "type": "string",
                "minLength": 1,
                "pattern": _RELATIVE_PATH_PATTERN,
            },
            "max_bytes": {"type": "integer", "minimum": 1},
            "media_type": {"type": "string", "minLength": 1},
            "required": {"type": "boolean"},
        },
    }


def _output_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["output_id", "digest", "size_bytes"],
        "properties": {
            "output_id": {"type": "string", "minLength": 1},
            "digest": {"type": "string", "pattern": _SHA256_PATTERN},
            "size_bytes": {"type": "integer", "minimum": 0},
            "media_type": {"type": "string", "minLength": 1},
        },
    }


def _bounded_state() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["max_bytes", "max_files", "max_directories"],
        "properties": {
            "max_bytes": {"type": "integer", "minimum": 0},
            "max_files": {"type": "integer", "minimum": 0},
            "max_directories": {"type": "integer", "minimum": 0},
        },
    }


def _network_policy() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["mode", "allowed_bindings"],
        "properties": {
            "mode": {"enum": [item.value for item in NetworkMode]},
            "allowed_bindings": _string_array(),
        },
        "allOf": [
            {
                "if": {"properties": {"mode": {"const": NetworkMode.NONE.value}}},
                "then": {"properties": {"allowed_bindings": {"maxItems": 0}}},
            },
            {
                "if": {"properties": {"mode": {"const": NetworkMode.BOUNDED.value}}},
                "then": {"properties": {"allowed_bindings": {"minItems": 1}}},
            },
        ],
    }


def _provider_binding() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "provider_id",
            "provider_kind",
            "provider_version",
            "provider_runtime_id",
            "roles",
            "capabilities",
            "metadata",
        ],
        "properties": {
            "provider_id": {"type": "string", "minLength": 1},
            "provider_kind": {"type": "string", "minLength": 1},
            "provider_version": {"type": ["string", "null"]},
            "provider_runtime_id": {"type": "string", "minLength": 1},
            "roles": {
                "type": "array",
                "items": {"enum": [item.value for item in ProviderRole]},
                "uniqueItems": True,
            },
            "capabilities": _capabilities(),
            "metadata": _string_map(),
        },
    }


def _command_result() -> dict[str, Any]:
    return {
        "type": ["object", "null"],
        "additionalProperties": False,
        "required": ["argv", "returncode", "stdout", "stderr"],
        "properties": {
            "argv": {"type": "array", "items": {"type": "string"}},
            "returncode": {"type": "integer"},
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
        },
    }


REQUEST_SCHEMA_DOCUMENT: dict[str, Any] = {
    "$schema": _JSON_SCHEMA,
    "$id": REQUEST_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "request_id",
        "workload",
        "required_capabilities",
        "preferred_capabilities",
        "isolation_floor",
        "persistence",
        "immutable_inputs",
        "outputs",
        "work_policy",
        "output_policy",
        "network_policy",
        "resource_requirements",
        "timeout_seconds",
        "environment_policy",
        "evidence_requirements",
    ],
    "properties": {
        "schema": {"const": REQUEST_SCHEMA},
        "request_id": {"type": "string", "minLength": 1},
        "workload": _workload(),
        "required_capabilities": _capabilities(),
        "preferred_capabilities": _capabilities(),
        "isolation_floor": {"enum": ["none", "state_only", "strong"]},
        "persistence": {"enum": [item.value for item in PersistenceMode]},
        "immutable_inputs": {
            "type": "array",
            "items": _immutable_input(),
            "uniqueItems": True,
        },
        "outputs": {
            "type": "array",
            "items": _output_spec(),
            "uniqueItems": True,
        },
        "work_policy": _bounded_state(),
        "output_policy": _bounded_state(),
        "network_policy": _network_policy(),
        "resource_requirements": _string_map(),
        "timeout_seconds": {
            "anyOf": [
                {"type": "number", "exclusiveMinimum": 0},
                {"type": "null"},
            ]
        },
        "environment_policy": {
            "type": "object",
            "additionalProperties": False,
            "required": ["allowed_keys", "secret_bindings"],
            "properties": {
                "allowed_keys": _string_array(),
                "secret_bindings": _string_array(),
            },
        },
        "evidence_requirements": _string_map(),
    },
}


LEASE_SCHEMA_DOCUMENT: dict[str, Any] = {
    "$schema": _JSON_SCHEMA,
    "$id": LEASE_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "request_id",
        "lease_id",
        "providers",
        "capabilities",
        "isolation_level",
        "persistence",
        "materialized_inputs",
        "network_policy",
        "work_policy",
        "output_policy",
        "owned_resource_id",
        "metadata",
    ],
    "properties": {
        "schema": {"const": LEASE_SCHEMA},
        "request_id": {"type": "string", "minLength": 1},
        "lease_id": {"type": "string", "minLength": 1},
        "providers": {
            "type": "array",
            "minItems": 1,
            "items": _provider_binding(),
        },
        "capabilities": _capabilities(),
        "isolation_level": {"enum": ["none", "state_only", "strong"]},
        "persistence": {"enum": [item.value for item in PersistenceMode]},
        "materialized_inputs": {
            "type": "array",
            "items": _immutable_input(),
            "uniqueItems": True,
        },
        "network_policy": _network_policy(),
        "work_policy": _bounded_state(),
        "output_policy": _bounded_state(),
        "owned_resource_id": {"type": "string", "minLength": 1},
        "metadata": _string_map(),
    },
}


RECEIPT_SCHEMA_DOCUMENT: dict[str, Any] = {
    "$schema": _JSON_SCHEMA,
    "$id": RECEIPT_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "request_id",
        "lease_id",
        "execution_id",
        "outcome",
        "providers",
        "workload",
        "immutable_inputs",
        "isolation_level",
        "network_policy",
        "command",
        "cleanup_state",
        "outputs",
        "diagnostic_codes",
        "metadata",
    ],
    "properties": {
        "schema": {"const": RECEIPT_SCHEMA},
        "request_id": {"type": "string", "minLength": 1},
        "lease_id": {"type": "string", "minLength": 1},
        "execution_id": {"type": "string", "minLength": 1},
        "outcome": {"enum": [item.value for item in ExecutionOutcome]},
        "providers": {
            "type": "array",
            "minItems": 1,
            "items": _provider_binding(),
        },
        "workload": _workload(),
        "immutable_inputs": {
            "type": "array",
            "items": _immutable_input(),
            "uniqueItems": True,
        },
        "isolation_level": {"enum": ["none", "state_only", "strong"]},
        "network_policy": _network_policy(),
        "command": _command_result(),
        "cleanup_state": {"enum": [item.value for item in CleanupState]},
        "outputs": {
            "type": "array",
            "items": _output_ref(),
            "uniqueItems": True,
        },
        "diagnostic_codes": _string_array(),
        "metadata": _string_map(),
    },
    "allOf": [
        {
            "if": {"properties": {"outcome": {"const": ExecutionOutcome.PASS.value}}},
            "then": {
                "properties": {"cleanup_state": {"const": CleanupState.CLEAN.value}}
            },
        }
    ],
}

_SCHEMA_DOCUMENTS = {
    REQUEST_SCHEMA: REQUEST_SCHEMA_DOCUMENT,
    LEASE_SCHEMA: LEASE_SCHEMA_DOCUMENT,
    RECEIPT_SCHEMA: RECEIPT_SCHEMA_DOCUMENT,
}


def schema_document(schema_id: str) -> dict[str, Any]:
    """Return an isolated copy of a stable machine-readable contract schema."""

    try:
        document = _SCHEMA_DOCUMENTS[schema_id]
    except KeyError as exc:
        raise ValueError(f"unknown execution schema: {schema_id}") from exc
    return deepcopy(document)
