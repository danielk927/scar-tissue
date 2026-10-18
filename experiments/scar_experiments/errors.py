"""Errors the env routes map to SPEC 7.4 status codes: 404, 422, 409 and 503."""


class NotFoundError(LookupError):
    """An ID in the request does not exist."""


class InvalidRequestError(ValueError):
    """The request is well-formed but not acceptable (wrong arm, excluded failure)."""


class StateConflictError(Exception):
    """Stored state conflicts with the request (a prompt version learned from another parent, no train pool yet)."""


class DependencyError(RuntimeError):
    """A dependency (agent service, env service, reflection LM, model endpoint) is unavailable or refused the call."""
