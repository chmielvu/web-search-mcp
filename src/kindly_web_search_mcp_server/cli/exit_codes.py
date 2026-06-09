from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    INTERNAL_ERROR = 1
    USAGE_ERROR = 2
    NOT_FOUND = 3
    AUTH_ERROR = 4
    CONFLICT = 5
    RATE_LIMITED = 6
    VALIDATION_ERROR = 7
    NETWORK_ERROR = 8
    SCHEMA_ERROR = 9
    PROVIDER_ERROR = 10
    PERMISSION_ERROR = 11
    TIMEOUT = 12
