"""Which model-call failures count as "the provider is unavailable".

The circuit breaker opens on failures that make every run fail regardless of its input:
transport, timeout, server errors, a wrong or malformed key, rate limiting, no balance, and
any 4xx the runtime left unclassified (HTTP_<status>). Failures caused by this turn's input
(context window, invalid request, empty response) do not count, and neither do codes that
identify nothing (UNKNOWN, the guard's ProviderError fallback). The vocabulary follows what
the shipped runtime emits: no balance is 'QUOTA' (QUOTA_EXCEEDED kept for older records).

Pure Python on purpose: importable by the unit tests without Frappe.
"""

PROVIDER_FAILURE_ERROR_CLASSES = (
    "TRANSPORT",
    "TIMEOUT",
    "SERVER",
    "AUTH",
    "INVALID_CREDENTIAL",
    "RATE_LIMIT",
    "QUOTA",
    "QUOTA_EXCEEDED",
)
UNCLASSIFIED_HTTP_PREFIX = "HTTP_"


def is_provider_failure(error_class):
    if not isinstance(error_class, str):
        return False
    return error_class in PROVIDER_FAILURE_ERROR_CLASSES or (
        error_class.startswith(UNCLASSIFIED_HTTP_PREFIX) and error_class[len(UNCLASSIFIED_HTTP_PREFIX):].isdigit()
    )


def count_provider_failures(error_classes):
    return sum(1 for error_class in error_classes if is_provider_failure(error_class))
