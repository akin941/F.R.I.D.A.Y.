"""Shared exception hierarchy. Kept deliberately small in MVP 0."""


class JarvisError(Exception):
    """Base class for all Jarvis OS domain errors."""


class WorkspaceScopingError(JarvisError):
    """Raised when code attempts to access business data without an actor scope."""
