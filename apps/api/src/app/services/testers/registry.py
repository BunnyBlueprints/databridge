"""Registry mapping connector types to connector tester implementations."""

from app.schemas.connection_test import ConnectorType
from app.services.testers.base import ConnectorTester
from app.services.testers.csv_tester import DEFAULT_MAX_FILE_BYTES, CsvConnectionTester
from app.services.testers.json_tester import JsonConnectionTester
from app.services.testers.mongo_tester import MongoDbConnectionTester
from app.services.testers.mysql_tester import MysqlConnectionTester
from app.services.testers.postgres_tester import PostgresConnectionTester
from app.services.testers.rest_tester import DEFAULT_MAX_RESPONSE_BYTES, RestApiConnectionTester


class ConnectorTesterRegistry:
    """In-memory dispatcher registry for connector testers."""

    def __init__(self) -> None:
        self._testers: dict[str, ConnectorTester] = {}

    def register(self, tester: ConnectorTester) -> None:
        """Register a tester for its declared connector type."""
        self._testers[tester.connector_type] = tester

    def get(self, connector_type: str) -> ConnectorTester | None:
        """Return the tester registered for `connector_type`, if implemented."""
        return self._testers.get(connector_type)

    def registered_connectors(self) -> tuple[ConnectorType, ...]:
        """Return currently registered connector types."""
        return tuple(tester.connector_type for tester in self._testers.values())


def create_default_tester_registry(
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_rest_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> ConnectorTesterRegistry:
    """Create the default tester registry with all six supported connector testers."""
    registry = ConnectorTesterRegistry()
    registry.register(PostgresConnectionTester())
    registry.register(MysqlConnectionTester())
    registry.register(MongoDbConnectionTester())
    registry.register(RestApiConnectionTester(max_response_bytes=max_rest_response_bytes))
    registry.register(CsvConnectionTester(max_file_bytes=max_file_bytes))
    registry.register(JsonConnectionTester(max_file_bytes=max_file_bytes))
    return registry
