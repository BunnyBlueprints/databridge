"""Read-only, bounded MongoDB collection and document-sampling schema discoverer."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pymongo
import pymongo.errors
from fastapi import status

from app.schemas.connection_test import (
    ConnectorType,
    MongoDbConnectionTestConfig,
    MongoDbConnectionTestCredentials,
    MongoDbConnectionTestRequest,
)
from app.schemas.connections import MongoDbPersistentConfig
from app.schemas.schemas import (
    SchemaObjectInput,
    SchemaWarning,
)
from app.services.credential_encryption import MongoDbStoredCredentials
from app.services.discovery.base import (
    DiscoveryContext,
    DiscoveryExecutionError,
    DiscoveryExecutionResult,
)
from app.services.discovery.inference import infer_documents_schema
from app.services.testers.mongo_tester import _classify_mongo_error, build_mongo_client_kwargs

logger = logging.getLogger("databridge.api")

MongoClientFactory = Callable[..., Any]


def _build_mongo_test_request(ctx: DiscoveryContext) -> MongoDbConnectionTestRequest:
    assert isinstance(ctx.config, MongoDbPersistentConfig)
    resolved_user = (
        ctx.credentials.username.get_secret_value()
        if isinstance(ctx.credentials, MongoDbStoredCredentials)
        and ctx.credentials.username is not None
        else ctx.legacy_username
    )
    password = (
        ctx.credentials.password if isinstance(ctx.credentials, MongoDbStoredCredentials) else None
    )
    conn_uri = (
        ctx.credentials.connectionUri
        if isinstance(ctx.credentials, MongoDbStoredCredentials)
        else None
    )
    creds = (
        MongoDbConnectionTestCredentials(password=password, connectionUri=conn_uri)
        if (password is not None or conn_uri is not None)
        else None
    )
    return MongoDbConnectionTestRequest(
        connectionId=ctx.connection_id,
        connectorType="MongoDB",
        config=MongoDbConnectionTestConfig(
            uri=ctx.config.uri,
            database=ctx.config.database,
            username=resolved_user,
        ),
        credentials=creds,
    )


def _http_status_for_mongo_error(error_code: str, is_timeout: bool) -> int:
    if is_timeout or error_code == "TIMEOUT":
        return status.HTTP_504_GATEWAY_TIMEOUT
    if error_code in ("AUTHENTICATION_FAILED", "INVALID_CONFIGURATION"):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_502_BAD_GATEWAY


async def _sample_collection_documents(
    collection: Any,
    *,
    sample_limit: int,
    max_time_ms: int,
) -> list[dict[str, Any]]:
    cursor = collection.find({}, limit=sample_limit, max_time_ms=max_time_ms)
    if hasattr(cursor, "to_list"):
        raw_docs = await cursor.to_list(length=sample_limit)
    else:
        raw_docs = []
        async for doc in cursor:
            raw_docs.append(doc)
            if len(raw_docs) >= sample_limit:
                break
    return [doc for doc in raw_docs if isinstance(doc, dict)]


class MongoDbSchemaDiscoverer:
    """Discovers MongoDB collections and infers nested BSON schemas from bounded samples."""

    connector_type: ConnectorType = "MongoDB"

    def __init__(self, client_factory: MongoClientFactory | None = None) -> None:
        self._client_factory: MongoClientFactory = client_factory or pymongo.AsyncMongoClient

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryExecutionResult:
        if not isinstance(ctx.config, MongoDbPersistentConfig):
            raise DiscoveryExecutionError(
                error_code="INVALID_CONFIGURATION",
                message="Invalid configuration dispatched to MongoDB discoverer.",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        client_kwargs = build_mongo_client_kwargs(
            _build_mongo_test_request(ctx), ctx.timeout_seconds
        )
        max_time_ms = max(250, int(ctx.timeout_seconds * 1000))
        client: Any = None
        warnings: list[SchemaWarning] = []
        partial = False

        try:
            async with asyncio.timeout(ctx.timeout_seconds):
                client = self._client_factory(**client_kwargs)
                db = client[ctx.config.database]
                coll_infos = await db.list_collections(filter={"type": "collection"})
                if hasattr(coll_infos, "to_list"):
                    coll_list = await coll_infos.to_list(length=ctx.max_objects + 1)
                elif isinstance(coll_infos, list):
                    coll_list = coll_infos
                else:
                    coll_list = [item async for item in coll_infos]

                coll_names = sorted(
                    str(item["name"])
                    for item in coll_list
                    if isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and not str(item["name"]).startswith("system.")
                )
                if len(coll_names) > ctx.max_objects:
                    coll_names = coll_names[: ctx.max_objects]
                    partial = True
                    warnings.append(
                        SchemaWarning(
                            code="OBJECT_LIMIT_REACHED",
                            message=(
                                f"Discovered collections truncated at limit of {ctx.max_objects}."
                            ),
                        )
                    )

                if not coll_names:
                    return DiscoveryExecutionResult(
                        discovery_status="completed",
                        discovery_source="inferred_sample",
                        objects=[],
                        warnings=[
                            SchemaWarning(
                                code="NO_OBJECTS_FOUND",
                                message=(
                                    f"No collections found in database '{ctx.config.database}'."
                                ),
                            )
                        ],
                        metadata={
                            "database": ctx.config.database,
                            "sampleLimit": ctx.sample_limit,
                        },
                    )

                objects: list[SchemaObjectInput] = []
                for coll_name in coll_names:
                    try:
                        docs = await _sample_collection_documents(
                            db[coll_name],
                            sample_limit=ctx.sample_limit,
                            max_time_ms=max_time_ms,
                        )
                    except pymongo.errors.OperationFailure:
                        partial = True
                        warnings.append(
                            SchemaWarning(
                                code="COLLECTION_ACCESS_DENIED",
                                message=(
                                    f"Unable to sample collection '{coll_name}' due to "
                                    "permissions or query failure."
                                ),
                                objectKey=coll_name,
                            )
                        )
                        continue

                    fields, coll_warnings, confidence = infer_documents_schema(
                        docs,
                        object_key=coll_name,
                        discovery_source="inferred_sample",
                        max_fields=ctx.max_fields_per_object,
                        primary_key_paths={"_id"},
                    )
                    warnings.extend(coll_warnings)
                    if any(w.code == "FIELD_LIMIT_REACHED" for w in coll_warnings):
                        partial = True

                    objects.append(
                        SchemaObjectInput(
                            objectKey=coll_name,
                            objectName=coll_name,
                            objectType="collection",
                            discoverySource="inferred_sample",
                            nativeMetadata={
                                "catalogName": ctx.config.database,
                                "sampleSize": len(docs),
                            },
                            canonicalMetadata={
                                "confidence": confidence,
                                "primaryKeyFields": (
                                    ["_id"] if any(f.fieldPath == "_id" for f in fields) else []
                                ),
                            },
                            fields=fields,
                        )
                    )
        except DiscoveryExecutionError:
            raise
        except Exception as exc:
            error_code, safe_message, is_timeout = _classify_mongo_error(exc)
            raise DiscoveryExecutionError(
                error_code=error_code,
                message=safe_message,
                http_status=_http_status_for_mongo_error(error_code, is_timeout),
                is_timeout=is_timeout,
            ) from exc
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as cleanup_exc:
                    logger.debug(
                        "MongoDB discovery client cleanup failed: %s",
                        type(cleanup_exc).__name__,
                    )

        return DiscoveryExecutionResult(
            discovery_status="partial" if partial else "completed",
            discovery_source="inferred_sample",
            objects=objects,
            warnings=warnings,
            metadata={"database": ctx.config.database, "sampleLimit": ctx.sample_limit},
        )
