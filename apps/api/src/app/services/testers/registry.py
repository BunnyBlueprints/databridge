"""Registry mapping connector types to connector tester implementations."""

from app.schemas.connection_test import ConnectorType
from app.services.testers.base import ConnectorTester
from app.services.testers.mongo_tester import MongoDbConnectionTester
from app.services.testers.mysql_tester import MysqlConnectionTester
from app.services.testers.postgres_tester import PostgresConnectionTester


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


def create_default_tester_registry() -> ConnectorTesterRegistry:
    """Create the default tester registry with database testers registered.

    Step 3 registers PostgreSQL, MySQL, and MongoDB testers.
    REST API, CSV, and JSON remain unimplemented until Step 4.
    """
    registry = ConnectorTesterRegistry()
    registry.register(PostgresConnectionTester())
    registry.register(MysqlConnectionTester())
    registry.register(MongoDbConnectionTester())
    return registry
