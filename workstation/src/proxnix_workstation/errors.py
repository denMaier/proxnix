class ProxnixWorkstationError(Exception):
    """Base error for the Python workstation package."""


class ConfigError(ProxnixWorkstationError):
    """Raised when the workstation config is invalid."""


class PlanningError(ProxnixWorkstationError):
    """Raised when a desired-state plan cannot be computed safely."""
