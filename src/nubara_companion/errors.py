class CompanionError(Exception):
    """Base class for all domain errors."""


class BookingNotFound(CompanionError):
    pass


class SourceUnavailable(CompanionError):
    """A read source timed out or is down."""


class ContractViolation(CompanionError):
    """A source returned a payload that does not match the expected contract."""


class ConfirmationRefused(CompanionError):
    """The requested rebooking cannot be confirmed in the current state."""
