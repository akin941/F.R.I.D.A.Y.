"""Shared exception hierarchy. Kept deliberately small in MVP 0."""


class JarvisError(Exception):
    """Base class for all Jarvis OS domain errors."""


class WorkspaceScopingError(JarvisError):
    """Raised when code attempts to access business data without an actor scope."""


class ApprovalRequiredError(JarvisError):
    """Raised when a write action is attempted without passing the approval gate.

    TODO(MVP 5): wired into the tool registry / approval gate.
    """
