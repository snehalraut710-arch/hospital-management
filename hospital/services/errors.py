"""Domain errors.

The service layer raises these; blueprints catch them and turn them into flash
messages. Keeping them distinct from HTTP concerns is what lets the services be
tested without a request context.
"""


class ServiceError(Exception):
    """Base class for anything the user should see a sensible message about."""


class SlotUnavailable(ServiceError):
    """The requested slot is outside the schedule, blocked, or already taken."""


class SessionFull(ServiceError):
    """The sitting has issued all of its tokens."""


class InvalidTransition(ServiceError):
    """An appointment or session was asked to move to a state it cannot reach."""


class NotPermitted(ServiceError):
    """The acting user does not own this record."""
