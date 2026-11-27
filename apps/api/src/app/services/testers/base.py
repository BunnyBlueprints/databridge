"""Protocol defining the interface for connector-specific testers."""

from typing import Protocol

from app.schemas.connection_test import (
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectorType,
)


class ConnectorTester(Protocol):
    """Contract implemented by each connector-specific test driver."""

    connector_type: ConnectorType

    async def test(
        self,
        request: ConnectionTestRequest,
        timeout_seconds: float,
    ) -> ConnectionTestResult:
        """Execute a connection test and return a normalized result."""
        ...
