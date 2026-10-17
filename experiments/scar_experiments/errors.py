"""Errors the env routes map to SPEC 7.4 status codes: 404, 422 and 503."""


class NotFoundError(LookupError):
    """An ID in the request does not exist."""


class InvalidRequestError(ValueError):
    """The request is well-formed but not acceptable (wrong arm, excluded failure)."""


class DependencyError(RuntimeError):
    """A dependency (agent service, env service, reflection LM) is unavailable."""
