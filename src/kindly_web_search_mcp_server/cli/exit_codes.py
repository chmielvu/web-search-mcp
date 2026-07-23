from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    INTERNAL_ERROR = 1
    USAGE_ERROR = 2
    AUTH_ERROR = 10
    PERMISSION_ERROR = 11
    NOT_FOUND = 20
    CONFLICT = 30
    RATE_LIMITED = 6
    VALIDATION_ERROR = 7
    NETWORK_ERROR = 8
    SCHEMA_ERROR = 9
    PROVIDER_ERROR = 12
    TIMEOUT = 13
