"""Unit tests for the JSON transient content tester."""

import logging

import pytest

from app.schemas.connection_test import (
    CsvConnectionTestConfig,
    CsvConnectionTestRequest,
    JsonConnectionTestConfig,
    JsonConnectionTestRequest,
)
from app.services.testers.json_tester import JsonConnectionTester


def _json_request(
    content: str,
    *,
    file_name: str = "events.json",
) -> JsonConnectionTestRequest:
    return JsonConnectionTestRequest(
        connectionId="conn-json-1",
        connectorType="JSON",
        config=JsonConnectionTestConfig(fileName=file_name),
        fileContent=content,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected_summary"),
    [
        ('{"id": 1, "active": true}', "top-level object with 2 keys"),
        ('[{"id": 1}, {"id": 2}]', "top-level array with 2 items"),
        ('"hello world"', "top-level string"),
        ("42.5", "top-level number"),
        ("true", "top-level boolean"),
        ("null", "top-level null"),
    ],
)
async def test_json_valid_types(payload: str, expected_summary: str) -> None:
    tester = JsonConnectionTester()
    res = await tester.test(_json_request(payload), timeout_seconds=5.0)

    assert res.success is True
    assert res.status == "connected"
    assert res.errorCode is None
    assert expected_summary in res.message


@pytest.mark.anyio
@pytest.mark.parametrize("empty_content", ["", "   \n\t  "])
async def test_json_empty_content_fails(empty_content: str) -> None:
    tester = JsonConnectionTester()
    res = await tester.test(_json_request(empty_content), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_json",
    [
        '{"secret_key": "sensitive_value", invalid}',
        '{"items": [1, 2,]}',
        '{"val": NaN}',
        '{"val": Infinity}',
    ],
)
async def test_json_invalid_syntax_returns_safe_error(bad_json: str) -> None:
    tester = JsonConnectionTester()
    res = await tester.test(_json_request(bad_json), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert "sensitive_value" not in res.message


@pytest.mark.anyio
async def test_json_bom_handling_and_invalid_encoding() -> None:
    tester = JsonConnectionTester()
    bom_res = await tester.test(_json_request('\ufeff{"ok": true}'), timeout_seconds=5.0)
    assert bom_res.success is True
    assert "top-level object with 1 keys" in bom_res.message

    bad_encoding = await tester.test(_json_request('{"bad": "\ufffd"}'), timeout_seconds=5.0)
    assert bad_encoding.success is False
    assert bad_encoding.errorCode == "INVALID_CONFIGURATION"

    surrogate = await tester.test(_json_request('{"bad": "\ud800"}'), timeout_seconds=5.0)
    assert surrogate.success is False
    assert surrogate.errorCode == "INVALID_CONFIGURATION"


@pytest.mark.anyio
async def test_json_wrong_extension_and_size_limit_and_no_content_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tester = JsonConnectionTester(max_file_bytes=32)

    bad_ext = JsonConnectionTestRequest.model_construct(
        connectorType="JSON",
        config=JsonConnectionTestConfig.model_construct(fileName="events.yaml"),
        fileContent='{"ok": true}',
    )
    ext_res = await tester.test(bad_ext, timeout_seconds=5.0)
    assert ext_res.success is False
    assert ext_res.errorCode == "INVALID_CONFIGURATION"

    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        oversized_res = await tester.test(
            _json_request('{"secret": "very_confidential_json_payload_123456"}'),
            timeout_seconds=5.0,
        )

    assert oversized_res.success is False
    assert oversized_res.errorCode == "INVALID_CONFIGURATION"
    assert "very_confidential_json_payload" not in oversized_res.message
    assert "very_confidential_json_payload" not in caplog.text


@pytest.mark.anyio
async def test_json_rejects_mismatched_request_type() -> None:
    tester = JsonConnectionTester()
    csv_req = CsvConnectionTestRequest(
        connectorType="CSV",
        config=CsvConnectionTestConfig(fileName="orders.csv"),
        fileContent="id\n1\n",
    )
    res = await tester.test(csv_req, timeout_seconds=5.0)
    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
