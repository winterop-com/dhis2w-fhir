"""FastAPI routers for a facade: mount the ones you want, in your own application."""

from dhis2w_fhir_engine.facade.api.auth import record_caller
from dhis2w_fhir_engine.facade.api.compose import MountedSurfaces, attach_facade, mount_facade

__all__ = ["MountedSurfaces", "attach_facade", "mount_facade", "record_caller"]
