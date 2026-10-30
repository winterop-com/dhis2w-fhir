"""What this software calls itself, on its CapabilityStatement and to the DHIS2 it reads."""

from __future__ import annotations

from importlib.metadata import version

#: The distribution the server reports as its software version.
DISTRIBUTION_NAME = "dhis2w-fhir-engine"


def server_version() -> str:
    """The installed version of this package, as the app and its CapabilityStatement report it."""
    return version(DISTRIBUTION_NAME)


def facade_provenance() -> str:
    """What a pass-through read names itself as to the instance: this software, and its version."""
    return f"{DISTRIBUTION_NAME}/{server_version()}"
