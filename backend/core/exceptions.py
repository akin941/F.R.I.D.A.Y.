"""Shared exception hierarchy. Kept deliberately small in MVP 0."""


class FridayError(Exception):
    """Base class for all Friday OS domain errors."""


class WorkspaceScopingError(FridayError):
    """Raised when code attempts to access business data without an actor scope."""
