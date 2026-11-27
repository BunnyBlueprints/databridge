"""Unit tests for the CSV transient content tester."""

import logging

import pytest

from app.schemas.connection_test import (
    CsvConnectionTestConfig,
    CsvConnectionTestRequest,
    JsonConnectionTestConfig,
    JsonConnectionTestRequest,
)
from app.services.testers.csv_tester import CsvConnectionTester
from app.services.testers.file_validation import FileValidationError, decode_utf8_file_content


def _csv_request(
    content: str,
    *,
    file_name: str = "orders.csv",
) -> CsvConnectionTestRequest:
    return CsvConnectionTestRequest(
        connectionId="conn-csv-1",
        connectorType="CSV",
        config=CsvConnectionTestConfig(fileName=file_name),
        fileContent=content,
    )


@pytest.mark.anyio
async def test_csv_valid_with_headers_and_rows() -> None:
    tester = CsvConnectionTester()
    res = await tester.test(
        _csv_request("id,customer,amount\n1,Alice,42.50\n\n2,Bob,19.00\n"),
        timeout_seconds=5.0,
    )

    assert res.success is True
    assert res.status == "connected"
    assert res.errorCode is None
    assert "3 columns" in res.message
    assert "2 data rows" in res.message


@pytest.mark.anyio
async def test_csv_valid_headers_with_zero_data_rows() -> None:
    tester = CsvConnectionTester()
    res = await tester.test(_csv_request("id,customer,amount\n"), timeout_seconds=5.0)

    assert res.success is True
    assert res.status == "connected"
    assert "0 data rows" in res.message


@pytest.mark.anyio
@pytest.mark.parametrize("empty_content", ["", "   \n\t\r\n"])
async def test_csv_empty_file_fails(empty_content: str) -> None:
    tester = CsvConnectionTester()
    res = await tester.test(_csv_request(empty_content), timeout_seconds=5.0)

    assert res.success is False
    assert res.status == "failed"
    assert res.errorCode == "INVALID_CONFIGURATION"


@pytest.mark.anyio
async def test_csv_missing_or_empty_headers_fail() -> None:
    tester = CsvConnectionTester()
    res = await tester.test(_csv_request("id,,amount\n1,Alice,10\n"), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert "empty column" in res.message.lower()


@pytest.mark.anyio
async def test_csv_duplicate_headers_fail() -> None:
    tester = CsvConnectionTester()
    res = await tester.test(_csv_request("id,name, id \n1,Alice,2\n"), timeout_seconds=5.0)

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert "duplicate" in res.message.lower()


@pytest.mark.anyio
async def test_csv_malformed_quotes_fail() -> None:
    tester = CsvConnectionTester()
    res = await tester.test(
        _csv_request('id,name\n1,"unclosed_secret_value\n'), timeout_seconds=5.0
    )

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert "unclosed_secret_value" not in res.message


@pytest.mark.anyio
async def test_csv_inconsistent_column_counts_fail_without_leaking_row_data() -> None:
    tester = CsvConnectionTester()
    res = await tester.test(
        _csv_request("id,name,email\n1,Alice,alice@example.com\n2,secret_row_cell\n"),
        timeout_seconds=5.0,
    )

    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
    assert "columns" in res.message.lower()
    assert "secret_row_cell" not in res.message


@pytest.mark.anyio
async def test_csv_bom_handling_and_invalid_encoding() -> None:
    tester = CsvConnectionTester()
    bom_res = await tester.test(_csv_request("\ufeffid,name\n1,Alice\n"), timeout_seconds=5.0)
    assert bom_res.success is True
    assert "2 columns" in bom_res.message

    bad_str_res = await tester.test(_csv_request("id,name\n1,\ufffd\n"), timeout_seconds=5.0)
    assert bad_str_res.success is False
    assert bad_str_res.errorCode == "INVALID_CONFIGURATION"

    with pytest.raises(FileValidationError):
        decode_utf8_file_content(b"\xff\xfe\xfd", max_bytes=1024)

    assert decode_utf8_file_content(b"\xef\xbb\xbfid,name\n", max_bytes=1024) == "id,name\n"


@pytest.mark.anyio
async def test_csv_wrong_extension_and_size_limit_and_no_content_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tester = CsvConnectionTester(max_file_bytes=32)

    bad_ext = CsvConnectionTestRequest.model_construct(
        connectorType="CSV",
        config=CsvConnectionTestConfig.model_construct(fileName="data.txt"),
        fileContent="id,name\n1,Alice\n",
    )
    ext_res = await tester.test(bad_ext, timeout_seconds=5.0)
    assert ext_res.success is False
    assert ext_res.errorCode == "INVALID_CONFIGURATION"

    with caplog.at_level(logging.DEBUG, logger="databridge.api"):
        oversized_res = await tester.test(
            _csv_request("id,secret_col\n1,very_confidential_csv_payload_1234567890\n"),
            timeout_seconds=5.0,
        )

    assert oversized_res.success is False
    assert oversized_res.errorCode == "INVALID_CONFIGURATION"
    assert "very_confidential_csv_payload" not in oversized_res.message
    assert "very_confidential_csv_payload" not in caplog.text


@pytest.mark.anyio
async def test_csv_rejects_mismatched_request_type() -> None:
    tester = CsvConnectionTester()
    json_req = JsonConnectionTestRequest(
        connectorType="JSON",
        config=JsonConnectionTestConfig(fileName="events.json"),
        fileContent="{}",
    )
    res = await tester.test(json_req, timeout_seconds=5.0)
    assert res.success is False
    assert res.errorCode == "INVALID_CONFIGURATION"
