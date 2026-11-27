"""Shared transient UTF-8 / BOM decoding and size-limit helpers for file testers."""


class FileValidationError(ValueError):
    """Raised when transient file content fails size, encoding, or format validation."""


def decode_utf8_file_content(
    raw_content: str | bytes,
    *,
    max_bytes: int,
) -> str:
    """Validate byte size, decode strict UTF-8, and strip a single leading UTF-8 BOM."""
    if isinstance(raw_content, str):
        if "\x00" in raw_content:
            raise FileValidationError("File content contains invalid null bytes.")
        if "\ufffd" in raw_content:
            raise FileValidationError("File content is not valid UTF-8 text.")
        try:
            raw_bytes = raw_content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FileValidationError("File content is not valid UTF-8 text.") from exc
    elif isinstance(raw_content, bytes):
        if b"\x00" in raw_content:
            raise FileValidationError("File content contains invalid null bytes.")
        raw_bytes = raw_content
    else:
        raise FileValidationError("File content must be a UTF-8 text string.")

    if len(raw_bytes) > max_bytes:
        raise FileValidationError(
            f"File content exceeds the maximum allowed size of {max_bytes} bytes."
        )

    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileValidationError("File content could not be decoded as valid UTF-8.") from exc

    if "\ufffd" in decoded:
        raise FileValidationError("File content is not valid UTF-8 text.")

    # Explicitly strip a single leading UTF-8 BOM if present.
    if decoded.startswith("\ufeff"):
        decoded = decoded[1:]

    return decoded
