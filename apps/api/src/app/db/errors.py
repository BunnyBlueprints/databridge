"""Safe database exception hierarchy (never embeds credentials or connection strings)."""


class DatabaseError(Exception):
    """Base error for database persistence operations."""


class DatabaseNotConfiguredError(DatabaseError):
    """Raised when persistence is invoked while DATABASE_URL is not configured."""


class DatabaseUnavailableError(DatabaseError):
    """Raised when the database cannot be reached or rejects connections."""


class DatabaseTimeoutError(DatabaseError):
    """Raised when connection acquisition or database execution times out."""


class DatabaseConflictError(DatabaseError):
    """Raised when a uniqueness or integrity constraint is violated."""


class MigrationError(DatabaseError):
    """Raised when migration discovery, validation, or execution fails."""
