"""REST API schema discoverer: OpenAPI JSON, SSRF-safe GET sample inference, and manual."""

import asyncio
import json
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import status

from app.schemas.connection_test import (
    ConnectorType,
    RestApiConnectionTestConfig,
    RestApiConnectionTestCredentials,
    RestApiConnectionTestRequest,
)
from app.schemas.schemas import (
    CanonicalFieldType,
    SchemaFieldInput,
    SchemaObjectInput,
    SchemaWarning,
)
from app.services.discovery.base import (
    DiscoveryContext,
    DiscoveryExecutionError,
    DiscoveryExecutionResult,
)
from app.services.discovery.file_discoverer import extract_json_records_for_inference
from app.services.discovery.inference import infer_documents_schema
from app.services.testers.json_tester import _reject_non_standard_constant
from app.services.testers.rest_tester import (
    DEFAULT_MAX_RESPONSE_BYTES,
    _build_auth_headers,
    _classify_http_exception,
    _is_redirect_target_safe,
)
from app.services.testers.ssrf import (
    AsyncHostResolver,
    SsrfBlockedError,
    SsrfSafeHttpTransport,
    resolve_and_validate_host,
    validate_http_target_url,
)

_HTTP_METHODS = ("get", "post", "put", "patch")
_OPENAPI_MAX_DEPTH = 8


def _resolve_same_origin_url(base_url: str, relative_path: str | None) -> str:
    if not relative_path:
        return base_url
    base_parts = urlsplit(base_url)
    target = urljoin(base_url if base_url.endswith("/") else f"{base_url}/", relative_path)
    target_parts = urlsplit(target)
    if target_parts.scheme != base_parts.scheme or target_parts.netloc != base_parts.netloc:
        raise DiscoveryExecutionError(
            error_code="INVALID_CONFIGURATION",
            message="Discovery path must remain on the same origin as the saved REST baseUrl.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return target


def _http_status_for_http_error(error_code: str, is_timeout: bool) -> int:
    if is_timeout or error_code == "TIMEOUT":
        return status.HTTP_504_GATEWAY_TIMEOUT
    if error_code in ("AUTHENTICATION_FAILED", "INVALID_CONFIGURATION"):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_502_BAD_GATEWAY


def _map_openapi_type(schema: dict[str, Any]) -> tuple[CanonicalFieldType, str]:
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        non_null = [t for t in raw_type if isinstance(t, str) and t != "null"]
        raw_type = non_null[0] if len(non_null) == 1 else ("mixed" if non_null else "null")
    type_str = str(raw_type).lower() if isinstance(raw_type, str) else ""
    fmt = str(schema.get("format", "")).lower()

    if type_str == "mixed":
        return "mixed", "openapi:mixed"
    if type_str == "null":
        return "null", "openapi:null"
    if type_str == "boolean":
        return "boolean", "openapi:boolean"
    if type_str == "integer":
        return "integer", f"openapi:integer({fmt})" if fmt else "openapi:integer"
    if type_str == "number":
        return "number", f"openapi:number({fmt})" if fmt else "openapi:number"
    if type_str == "array":
        return "array", "openapi:array"
    if type_str == "object":
        return "object", "openapi:object"
    if type_str == "string":
        if fmt == "date":
            return "date", "openapi:string(date)"
        if fmt in ("date-time", "datetime"):
            return "datetime", f"openapi:string({fmt})"
        if fmt in ("byte", "binary"):
            return "binary", f"openapi:string({fmt})"
        return "string", f"openapi:string({fmt})" if fmt else "openapi:string"
    if "properties" in schema or "additionalProperties" in schema:
        return "object", "openapi:object"
    return "unknown", "openapi:unknown"


def _resolve_local_json_pointer(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    current: Any = doc
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


@dataclass
class _OpenApiWalkState:
    object_key: str
    max_fields: int
    fields: list[SchemaFieldInput] = field(default_factory=list)
    seen_paths: set[str] = field(default_factory=set)
    warnings: list[SchemaWarning] = field(default_factory=list)
    truncated_fields: bool = False


def _extract_openapi_fields(
    doc: dict[str, Any],
    root_schema: dict[str, Any],
    *,
    object_key: str,
    max_fields: int,
) -> tuple[list[SchemaFieldInput], list[SchemaWarning], bool]:
    state = _OpenApiWalkState(object_key=object_key, max_fields=max_fields)

    def _add_field(
        *,
        field_path: str,
        field_name: str,
        parent_path: str | None,
        depth: int,
        canonical_type: CanonicalFieldType,
        native_type: str,
        nullable: bool,
        is_required: bool,
        is_array: bool,
        extra_meta: dict[str, Any] | None = None,
    ) -> bool:
        if field_path in state.seen_paths:
            return False
        if len(state.fields) >= state.max_fields:
            state.truncated_fields = True
            return False
        state.seen_paths.add(field_path)
        metadata: dict[str, Any] = {"depth": depth, "isArray": is_array}
        if parent_path is not None:
            metadata["parentPath"] = parent_path
        if extra_meta:
            metadata.update(extra_meta)
        state.fields.append(
            SchemaFieldInput(
                fieldPath=field_path,
                fieldName=field_name,
                nativeType=native_type[:128],
                canonicalType=canonical_type,
                nullable=nullable,
                isRequired=is_required,
                ordinalPosition=len(state.fields),
                discoverySource="openapi",
                metadata=metadata,
            )
        )
        return True

    def _resolve_schema(
        schema: dict[str, Any],
        *,
        visited_refs: frozenset[str],
        path_hint: str,
    ) -> tuple[dict[str, Any], frozenset[str], bool]:
        if "$ref" not in schema:
            return schema, visited_refs, False
        ref_val = str(schema["$ref"])
        if not ref_val.startswith("#/"):
            state.warnings.append(
                SchemaWarning(
                    code="EXTERNAL_REF_SKIPPED",
                    message=(
                        f"Skipped external OpenAPI $ref at '{path_hint}'; "
                        "external references are not fetched."
                    ),
                    objectKey=object_key,
                    fieldPath=path_hint,
                )
            )
            return {"type": "unknown", "_externalRef": True}, visited_refs, True
        if ref_val in visited_refs:
            state.warnings.append(
                SchemaWarning(
                    code="CIRCULAR_REF_TRUNCATED",
                    message=f"Truncated circular local $ref at '{path_hint}'.",
                    objectKey=object_key,
                    fieldPath=path_hint,
                )
            )
            return {"type": "object", "_circularRef": True}, visited_refs, True
        target = _resolve_local_json_pointer(doc, ref_val)
        if target is None:
            state.warnings.append(
                SchemaWarning(
                    code="UNRESOLVED_LOCAL_REF",
                    message=f"Could not resolve local $ref at '{path_hint}'.",
                    objectKey=object_key,
                    fieldPath=path_hint,
                )
            )
            return {"type": "unknown"}, visited_refs, True
        return target, visited_refs | {ref_val}, False

    def _walk_properties(
        schema: dict[str, Any],
        *,
        parent_path: str | None,
        depth: int,
        visited_refs: frozenset[str],
    ) -> None:
        if depth > _OPENAPI_MAX_DEPTH:
            state.warnings.append(
                SchemaWarning(
                    code="MAX_DEPTH_TRUNCATED",
                    message=(
                        f"OpenAPI nested schema in '{object_key}' exceeded depth "
                        f"{_OPENAPI_MAX_DEPTH}."
                    ),
                    objectKey=object_key,
                    fieldPath=parent_path,
                )
            )
            return

        resolved, next_refs, stopped = _resolve_schema(
            schema, visited_refs=visited_refs, path_hint=parent_path or object_key
        )
        if stopped:
            return

        if "allOf" in resolved and isinstance(resolved["allOf"], list):
            for sub in resolved["allOf"]:
                if isinstance(sub, dict):
                    _walk_properties(
                        sub,
                        parent_path=parent_path,
                        depth=depth,
                        visited_refs=next_refs,
                    )

        raw_type = resolved.get("type")
        if raw_type == "array" and isinstance(resolved.get("items"), dict):
            item_schema = resolved["items"]
            item_resolved, item_refs, item_stopped = _resolve_schema(
                item_schema,
                visited_refs=next_refs,
                path_hint=f"{parent_path}[]" if parent_path else "[]",
            )
            if item_stopped:
                return
            _walk_properties(
                item_resolved,
                parent_path=parent_path,
                depth=depth,
                visited_refs=item_refs,
            )
            return

        properties = resolved.get("properties")
        if not isinstance(properties, dict):
            return

        required_set = {str(item) for item in resolved.get("required", []) if isinstance(item, str)}
        for prop_name in sorted(properties.keys(), key=str):
            prop_schema_raw = properties[prop_name]
            if not isinstance(prop_schema_raw, dict):
                continue
            field_path = f"{parent_path}.{prop_name}" if parent_path else prop_name
            prop_schema, prop_refs, prop_stopped = _resolve_schema(
                prop_schema_raw, visited_refs=next_refs, path_hint=field_path
            )
            canonical_type, native_type = _map_openapi_type(prop_schema)
            if prop_schema.get("_externalRef"):
                native_type = "openapi:external_ref"
            raw_type_field = prop_schema.get("type")
            allows_null = bool(prop_schema.get("nullable", False)) or (
                isinstance(raw_type_field, list) and "null" in raw_type_field
            )
            is_required = prop_name in required_set
            nullable = allows_null or not is_required

            extra_meta: dict[str, Any] = {}
            if isinstance(prop_schema.get("enum"), list):
                extra_meta["enumCount"] = len(prop_schema["enum"])

            added = _add_field(
                field_path=field_path,
                field_name=str(prop_name),
                parent_path=parent_path,
                depth=depth,
                canonical_type=canonical_type,
                native_type=native_type,
                nullable=nullable,
                is_required=is_required,
                is_array=canonical_type == "array",
                extra_meta=extra_meta or None,
            )
            if not added or prop_stopped:
                continue

            if canonical_type == "object":
                _walk_properties(
                    prop_schema,
                    parent_path=field_path,
                    depth=depth + 1,
                    visited_refs=prop_refs,
                )
            elif canonical_type == "array" and isinstance(prop_schema.get("items"), dict):
                elem_path = f"{field_path}[]"
                elem_raw = prop_schema["items"]
                elem_schema, elem_refs, elem_stopped = _resolve_schema(
                    elem_raw, visited_refs=prop_refs, path_hint=elem_path
                )
                elem_canonical, elem_native = _map_openapi_type(elem_schema)
                elem_nullable = bool(elem_schema.get("nullable", False))
                elem_added = _add_field(
                    field_path=elem_path,
                    field_name=f"{prop_name}[]",
                    parent_path=field_path,
                    depth=depth + 1,
                    canonical_type=elem_canonical,
                    native_type=elem_native,
                    nullable=elem_nullable,
                    is_required=False,
                    is_array=elem_canonical == "array",
                )
                if elem_added and not elem_stopped and elem_canonical == "object":
                    _walk_properties(
                        elem_schema,
                        parent_path=elem_path,
                        depth=depth + 2,
                        visited_refs=elem_refs,
                    )

    _walk_properties(root_schema, parent_path=None, depth=0, visited_refs=frozenset())
    if state.truncated_fields:
        state.warnings.append(
            SchemaWarning(
                code="FIELD_LIMIT_REACHED",
                message=(
                    f"OpenAPI object '{object_key}' exceeded {max_fields} fields and was truncated."
                ),
                objectKey=object_key,
            )
        )
    return state.fields, state.warnings, state.truncated_fields


def _extract_schema_from_operation(
    doc: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any] | None:
    responses = operation.get("responses")
    if isinstance(responses, dict):
        for code in ("200", "201", "default"):
            resp_obj = responses.get(code)
            if not isinstance(resp_obj, dict):
                continue
            content = resp_obj.get("content")
            if isinstance(content, dict) and isinstance(content.get("application/json"), dict):
                schema = content["application/json"].get("schema")
                if isinstance(schema, dict):
                    return schema
            if isinstance(resp_obj.get("schema"), dict):
                return resp_obj["schema"]

    req_body = operation.get("requestBody")
    if isinstance(req_body, dict):
        content = req_body.get("content")
        if isinstance(content, dict) and isinstance(content.get("application/json"), dict):
            schema = content["application/json"].get("schema")
            if isinstance(schema, dict):
                return schema
    return None


def parse_openapi_json_document(
    doc: dict[str, Any],
    *,
    max_objects: int,
    max_fields_per_object: int,
) -> DiscoveryExecutionResult:
    """Parse an OpenAPI 3.x / Swagger 2.0 JSON document using local #/ refs only."""
    if not isinstance(doc, dict) or ("openapi" not in doc and "swagger" not in doc):
        raise DiscoveryExecutionError(
            error_code="INVALID_CONFIGURATION",
            message="Provided document is not a valid OpenAPI 3.x or Swagger 2.0 JSON spec.",
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    objects: list[SchemaObjectInput] = []
    warnings: list[SchemaWarning] = []
    partial = False

    paths = doc.get("paths")
    if isinstance(paths, dict):
        for path_str in sorted(paths.keys(), key=str):
            path_item = paths[path_str]
            if not isinstance(path_item, dict):
                continue
            for method in _HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                schema_node = _extract_schema_from_operation(doc, operation)
                if schema_node is None:
                    continue
                if len(objects) >= max_objects:
                    partial = True
                    warnings.append(
                        SchemaWarning(
                            code="OBJECT_LIMIT_REACHED",
                            message=f"OpenAPI discovery truncated at {max_objects} objects.",
                        )
                    )
                    break

                object_key = f"{method.upper()} {path_str}"
                fields, obj_warnings, truncated_fields = _extract_openapi_fields(
                    doc,
                    schema_node,
                    object_key=object_key,
                    max_fields=max_fields_per_object,
                )
                warnings.extend(obj_warnings)
                if truncated_fields:
                    partial = True

                objects.append(
                    SchemaObjectInput(
                        objectKey=object_key,
                        objectName=str(operation.get("operationId") or object_key)[:255],
                        objectType="endpoint",
                        discoverySource="openapi",
                        nativeMetadata={
                            "httpMethod": method.upper(),
                            "relativePath": str(path_str),
                        },
                        canonicalMetadata={
                            "confidence": 1.0,
                            "primaryKeyFields": [],
                        },
                        fields=fields,
                    )
                )
            if len(objects) >= max_objects and partial:
                break

    if not objects:
        schemas_map = (
            doc.get("components", {}).get("schemas")
            if isinstance(doc.get("components"), dict)
            else doc.get("definitions")
        )
        if isinstance(schemas_map, dict):
            for name in sorted(schemas_map.keys(), key=str):
                schema_node = schemas_map[name]
                if not isinstance(schema_node, dict):
                    continue
                if len(objects) >= max_objects:
                    partial = True
                    warnings.append(
                        SchemaWarning(
                            code="OBJECT_LIMIT_REACHED",
                            message=f"OpenAPI discovery truncated at {max_objects} objects.",
                        )
                    )
                    break
                fields, obj_warnings, truncated_fields = _extract_openapi_fields(
                    doc,
                    schema_node,
                    object_key=str(name),
                    max_fields=max_fields_per_object,
                )
                warnings.extend(obj_warnings)
                if truncated_fields:
                    partial = True
                objects.append(
                    SchemaObjectInput(
                        objectKey=str(name),
                        objectName=str(name)[:255],
                        objectType="endpoint",
                        discoverySource="openapi",
                        nativeMetadata={"schemaComponent": str(name)},
                        canonicalMetadata={"confidence": 1.0, "primaryKeyFields": []},
                        fields=fields,
                    )
                )

    if not objects:
        warnings.append(
            SchemaWarning(
                code="NO_OBJECTS_FOUND",
                message="OpenAPI specification did not contain discoverable JSON object schemas.",
            )
        )

    return DiscoveryExecutionResult(
        discovery_status="partial" if partial else "completed",
        discovery_source="openapi",
        objects=objects,
        warnings=warnings,
        metadata={"specVersion": str(doc.get("openapi") or doc.get("swagger") or "")},
    )


class RestApiSchemaDiscoverer:
    """Discovers REST API schemas via OpenAPI JSON, bounded SSRF-safe GET, or manual specs."""

    connector_type: ConnectorType = "REST API"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: AsyncHostResolver | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._transport = transport
        self._resolver = resolver
        self._max_response_bytes = max_response_bytes

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if not isinstance(ctx.config, RestApiConnectionTestConfig):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid configuration dispatched to REST API discoverer.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        mode = ctx.options.mode
        if mode is None:
            if ctx.options.manualObjects:
                mode = "manual"
            elif ctx.options.openApiSpec is not None or ctx.options.openApiPath is not None:
                mode = "openapi"
            else:
                mode = "sample_get"

        if mode == "manual":
            return self._discover_manual(ctx)
        if mode == "openapi":
            return await self._discover_openapi(ctx)
        return await self._discover_sample_get(ctx)

    def _discover_manual(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        manual_objs = ctx.options.manualObjects
        if not manual_objs:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Manual REST discovery requires at least one manualObjects entry.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        if len(manual_objs) > ctx.max_objects:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message=f"Manual discovery exceeds maximum object count of {ctx.max_objects}.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        objects: list[SchemaObjectInput] = []
        for manual_obj in manual_objs:
            if len(manual_obj.fields) > ctx.max_fields_per_object:
                raise DiscoveryExecutionError(
                    error_code="INVALID_CONFIGURATION",
                    message=(
                        f"Manual object '{manual_obj.objectKey}' exceeds "
                        f"{ctx.max_fields_per_object} fields."
                    ),
                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            fields = [
                SchemaFieldInput(
                    fieldPath=fld.fieldPath,
                    fieldName=fld.fieldName or fld.fieldPath.split(".")[-1],
                    nativeType=fld.nativeType or f"manual:{fld.canonicalType}",
                    canonicalType=fld.canonicalType,
                    nullable=fld.nullable,
                    isRequired=fld.isRequired,
                    primaryKey=fld.primaryKey,
                    uniqueKey=fld.uniqueKey,
                    defaultMetadata=fld.defaultMetadata,
                    ordinalPosition=idx,
                    discoverySource="manual",
                    metadata=fld.metadata,
                )
                for idx, fld in enumerate(manual_obj.fields)
            ]
            objects.append(
                SchemaObjectInput(
                    objectKey=manual_obj.objectKey,
                    objectName=manual_obj.objectName or manual_obj.objectKey,
                    objectType=manual_obj.objectType,
                    discoverySource="manual",
                    nativeMetadata=manual_obj.nativeMetadata,
                    canonicalMetadata={
                        "confidence": 1.0,
                        "primaryKeyFields": [f.fieldPath for f in fields if f.primaryKey],
                    },
                    fields=fields,
                )
            )

        return DiscoveryExecutionResult(
            discovery_status="completed",
            discovery_source="manual",
            objects=objects,
            warnings=[],
            metadata={"mode": "manual"},
        )

    async def _discover_openapi(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if ctx.options.openApiSpec is not None:
            return parse_openapi_json_document(
                ctx.options.openApiSpec,
                max_objects=ctx.max_objects,
                max_fields_per_object=ctx.max_fields_per_object,
            )

        assert isinstance(ctx.config, RestApiConnectionTestConfig)
        target_path = ctx.options.openApiPath or "/openapi.json"
        target_url = _resolve_same_origin_url(ctx.config.baseUrl, target_path)
        parsed_json = await self._fetch_bounded_json(ctx, target_url=target_url)
        if not isinstance(parsed_json, dict):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="OpenAPI specification endpoint must return a JSON object.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return parse_openapi_json_document(
            parsed_json,
            max_objects=ctx.max_objects,
            max_fields_per_object=ctx.max_fields_per_object,
        )

    async def _discover_sample_get(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        assert isinstance(ctx.config, RestApiConnectionTestConfig)
        target_url = _resolve_same_origin_url(ctx.config.baseUrl, ctx.options.resourcePath)
        parsed_json = await self._fetch_bounded_json(ctx, target_url=target_url)

        rel_path = ctx.options.resourcePath or (urlsplit(target_url).path or "/")
        object_key = f"GET {rel_path}"
        records, extract_warnings = extract_json_records_for_inference(
            parsed_json,
            record_path=ctx.options.recordPath,
            sample_limit=ctx.sample_limit,
            source_label="REST API response",
        )
        fields, infer_warnings, confidence = infer_documents_schema(
            records,
            object_key=object_key,
            discovery_source="inferred_sample",
            max_fields=ctx.max_fields_per_object,
        )
        warnings = [*extract_warnings, *infer_warnings]
        is_partial = any(w.code in ("FIELD_LIMIT_REACHED", "MAX_DEPTH_TRUNCATED") for w in warnings)

        obj = SchemaObjectInput(
            objectKey=object_key,
            objectName=rel_path,
            objectType="endpoint",
            discoverySource="inferred_sample",
            nativeMetadata={
                "httpMethod": "GET",
                "relativePath": rel_path,
                "sampleSize": len(records),
                **({"recordPath": ctx.options.recordPath} if ctx.options.recordPath else {}),
            },
            canonicalMetadata={
                "confidence": confidence,
                "primaryKeyFields": [],
            },
            fields=fields,
        )
        return DiscoveryExecutionResult(
            discovery_status="partial" if is_partial else "completed",
            discovery_source="inferred_sample",
            objects=[obj],
            warnings=warnings,
            metadata={"mode": "sample_get", "sampleLimit": ctx.sample_limit},
        )

    async def _fetch_bounded_json(self, ctx: DiscoveryContext, *, target_url: str) -> Any:
        assert isinstance(ctx.config, RestApiConnectionTestConfig)
        rest_creds = (
            ctx.credentials
            if isinstance(ctx.credentials, RestApiConnectionTestCredentials)
            else None
        )
        test_req = RestApiConnectionTestRequest(
            connectionId=ctx.connection_id,
            connectorType="REST API",
            config=ctx.config,
            credentials=rest_creds,
        )
        try:
            headers = _build_auth_headers(test_req)
        except ValueError as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        effective_timeout = max(0.2, float(ctx.timeout_seconds))
        try:
            hostname, port = validate_http_target_url(target_url)
            if self._transport is not None:
                await resolve_and_validate_host(
                    hostname,
                    port,
                    resolver=self._resolver,
                    timeout=effective_timeout,
                )
        except SsrfBlockedError as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Target URL or resolved network address is not permitted.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc
        except TimeoutError as exc:
            raise DiscoveryExecutionError(
                error_code="TIMEOUT",
                message="REST API discovery timed out while resolving hostname.",
                http_status=status.HTTP_504_GATEWAY_TIMEOUT,
                is_timeout=True,
            ) from exc
        except socket.gaierror as exc:
            raise DiscoveryExecutionError(
                error_code="DNS_RESOLUTION_FAILED",
                message="Unable to resolve the REST API hostname.",
                http_status=status.HTTP_502_BAD_GATEWAY,
            ) from exc

        transport = self._transport or SsrfSafeHttpTransport(resolver=self._resolver)
        timeout_cfg = httpx.Timeout(effective_timeout)

        try:
            async with asyncio.timeout(effective_timeout):
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=timeout_cfg,
                    follow_redirects=False,
                    trust_env=False,
                    max_redirects=0,
                ) as client:
                    async with client.stream("GET", target_url, headers=headers) as response:
                        sc = response.status_code
                        if 300 <= sc < 400:
                            safe_redirect = _is_redirect_target_safe(
                                response.headers.get("location"),
                                base_url=target_url,
                            )
                            msg = (
                                "REST API endpoint attempted to redirect to a prohibited "
                                "destination."
                                if not safe_redirect
                                else f"REST API endpoint returned HTTP {sc} redirect; "
                                "automatic redirects are disabled."
                            )
                            raise DiscoveryExecutionError(
                                error_code="INVALID_CONFIGURATION",
                                message=msg,
                                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            )
                        if sc in (401, 403):
                            raise DiscoveryExecutionError(
                                error_code="AUTHENTICATION_FAILED",
                                message=f"REST API authentication failed (HTTP {sc}).",
                                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            )
                        if sc >= 400:
                            err_code = "SERVER_ERROR" if sc >= 500 else "INVALID_CONFIGURATION"
                            http_err = (
                                status.HTTP_502_BAD_GATEWAY
                                if sc >= 500
                                else status.HTTP_422_UNPROCESSABLE_CONTENT
                            )
                            raise DiscoveryExecutionError(
                                error_code=err_code,
                                message=f"REST API discovery request returned HTTP {sc}.",
                                http_status=http_err,
                            )

                        buf = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=4096):
                            remaining = self._max_response_bytes - len(buf) + 1
                            buf.extend(chunk[:remaining])
                            if len(buf) > self._max_response_bytes:
                                raise DiscoveryExecutionError(
                                    error_code="INVALID_CONFIGURATION",
                                    message=(
                                        f"REST API response exceeded maximum size of "
                                        f"{self._max_response_bytes} bytes."
                                    ),
                                    http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                )
        except DiscoveryExecutionError:
            raise
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_http_exception(exc)
            raise DiscoveryExecutionError(
                error_code=error_code,
                message=safe_message,
                http_status=_http_status_for_http_error(error_code, is_timeout),
                is_timeout=is_timeout,
            ) from exc

        try:
            text = bytes(buf).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="REST API discovery response was not valid UTF-8 JSON.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

        try:
            return json.loads(text, parse_constant=_reject_non_standard_constant)
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="REST API discovery response was not valid JSON.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc
