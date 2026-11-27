"""Registry mapping connector types to schema discoverer implementations."""

from app.schemas.connection_test import ConnectorType
from app.services.discovery.base import ConnectorDiscoverer
from app.services.discovery.file_discoverer import CsvSchemaDiscoverer, JsonSchemaDiscoverer
from app.services.discovery.mongodb_discoverer import MongoDbSchemaDiscoverer
from app.services.discovery.mysql_discoverer import MysqlSchemaDiscoverer
from app.services.discovery.postgres_discoverer import PostgresSchemaDiscoverer
from app.services.discovery.rest_discoverer import RestApiSchemaDiscoverer
from app.services.testers.csv_tester import DEFAULT_MAX_FILE_BYTES
from app.services.testers.rest_tester import DEFAULT_MAX_RESPONSE_BYTES


class ConnectorDiscovererRegistry:
    """In-memory dispatcher registry for connector schema discoverers."""

    def __init__(self) -> None:
        self._discoverers: dict[str, ConnectorDiscoverer] = {}

    def register(self, discoverer: ConnectorDiscoverer) -> None:
        """Register a schema discoverer for its declared connector type."""
        self._discoverers[discoverer.connector_type] = discoverer

    def get(self, connector_type: str) -> ConnectorDiscoverer | None:
        """Return the registered discoverer for `connector_type`, if supported."""
        return self._discoverers.get(connector_type)

    def registered_connectors(self) -> tuple[ConnectorType, ...]:
        """Return all registered connector types."""
        return tuple(discoverer.connector_type for discoverer in self._discoverers.values())


def create_default_discoverer_registry(
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_rest_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> ConnectorDiscovererRegistry:
    """Create the default discoverer registry with all six supported connectors."""
    registry = ConnectorDiscovererRegistry()
    registry.register(PostgresSchemaDiscoverer())
    registry.register(MysqlSchemaDiscoverer())
    registry.register(MongoDbSchemaDiscoverer())
    registry.register(RestApiSchemaDiscoverer(max_response_bytes=max_rest_response_bytes))
    registry.register(CsvSchemaDiscoverer(max_file_bytes=max_file_bytes))
    registry.register(JsonSchemaDiscoverer(max_file_bytes=max_file_bytes))
    return registry
