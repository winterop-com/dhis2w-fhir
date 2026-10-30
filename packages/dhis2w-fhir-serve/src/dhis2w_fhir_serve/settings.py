"""The settings the FHIR facade app factory is built from, and how one invocation resolves them."""

from __future__ import annotations

from pathlib import Path

from dhis2w_client.profile import NoProfileError
from dhis2w_fhir.service import GenerationProfile, resolve_generation_profile
from dhis2w_fhir_engine.facade.api.settings import ServeSettings
from dhis2w_fhir_engine.facade.config import (
    FhirProject,
    ServeAuth,
    ServeAuthScope,
    basemaps_from_options,
)
from dhis2w_fhir_engine.facade.registry_package import resolve_registry_source
from dhis2w_fhir_engine.facade.store import COMPILED_RESOURCES_RELATIVE_PATH, CompiledIgMissingError
from pydantic import BaseModel, ConfigDict

from dhis2w_fhir_serve.auth_setup import preflight_auth


class ServeInvocation(BaseModel):
    """What one run of the facade resolved to: what it serves, where it listens, and the profile behind it.

    The address is here rather than on `ServeSettings` because it is what the process binds and not
    what the app serves - the factory reads the settings and never asks where the socket is. The
    resolved profile is here for the sharper reason: it carries the credentials the store connects
    with, and the settings are handed to the app and read out again by `/facade/uiconfig`, so a profile on
    them would be a credential one HTTP response away from a browser. What the screens are entitled
    to - the instance's address - is on the settings as `dhis2_base_url`, and the name and the origin
    stay here, where the command that resolved them can say which profile it used.
    """

    model_config = ConfigDict(frozen=True)

    settings: ServeSettings
    host: str
    port: int
    generation: GenerationProfile | None = None


def _resolved_generation(project: FhirProject, profile: str | None, *, required: bool) -> GenerationProfile | None:
    """The DHIS2 instance this run is about, or None when this machine names no profile at all.

    Absence and error are different answers. A machine with no `profiles.toml` anywhere is a
    compiled guide being served on its own, so it resolves to None. A profile that IS named - by
    `d2w -p`, by `DHIS2_PROFILE`, or by fhir.toml - and turns out not to exist is a statement that
    is wrong, and it refuses the run whether or not a live store needed to connect.
    """
    try:
        return resolve_generation_profile(project, profile)
    except NoProfileError:
        if required:
            raise
        return None


def resolve_serve_settings(
    project: FhirProject,
    *,
    live: bool = False,
    host: str | None = None,
    port: int | None = None,
    strict_codes: bool | None = None,
    ui: bool | None = None,
    basemaps: list[str] | None = None,
    profile: str | None = None,
    auth: ServeAuth | None = None,
    auth_scope: ServeAuthScope | None = None,
    registry_package: Path | None = None,
) -> ServeInvocation:
    """Resolve one invocation of the facade: a stated dial wins, then `[serve]`, then this model's defaults.

    Every argument is what one run stated and None is "stated nothing", which is why the
    booleans are `bool | None` rather than `bool`: `--no-ui` on a project whose table says
    `ui = true` has to be able to say False and be heard. `live` is the exception and takes a
    plain bool, because no `[serve]` key answers it - a live store is a property of the run.

    `basemaps` are the raw `--basemap` strings, read through `basemaps_from_options`; stating
    none leaves the table's layers alone. A value that names nothing servable raises
    `ValueError`, which is the caller's to render - `d2w fhir serve` renders it against the
    flag the value came from.

    Two of `[serve]` have no dial at all and are carried across verbatim: `capture`, which says
    what this server offers rather than what one run does, and `spool_dir`, which says where the
    receipts a previous run wrote already live.

    The profile is resolved here, before anything is built, so a named profile that does not
    exist refuses the run rather than failing under a banner saying the server is starting. A
    machine that names no profile at all resolves to None and serves its compiled guide offline,
    which is a whole posture rather than a degraded one; `live` makes the profile required,
    since a live store has an instance to read. The address it resolved is what the screens link
    an identity out to, and it is the only part of the profile that reaches the settings - the
    name, the origin, and the credentials stay on the invocation, which never leaves the process
    that opened it.

    The compiled guide is checked last, and only when the store is the one on disk: a project
    that has never run SUSHI has nothing to serve, and refusing here says so in one line rather
    than answering every read with a 404.

    THE AUTH POSTURE IS PREFLIGHTED HERE TOO, and for the same reason every other refusal is: a
    server that starts and then cannot honour what it was asked for is a failure nobody reads
    until somebody meets it. Five refusals, in `dhis2w_fhir_engine.facade.api.auth.preflight_auth`: an
    interface other than loopback while neither this run nor fhir.toml has stated a posture, the
    `token` posture with its environment variable unset, the `dhis2` posture on a run that reads
    a compiled guide and so has no instance to check anybody against, the `jwt` posture with no
    `[serve.jwt] issuer`, and `[serve.jwt] forward_bearer` on a run with no instance to forward
    to. Whether the posture was STATED is what the first of those turns on - `auth = "none"`
    written down is a decision, an absent key is not - so a stated flag counts as much as a
    stated table key, and only the two of them being silent is silence.

    The sixth thing that could stop a `jwt` run - an issuer this machine cannot reach - is a
    round trip, so it is not made here: `open_serve_runtime` reads the issuer while the server
    starts and refuses with the same error type. This function reads values.
    """
    serve_config = project.config.serve
    stated_basemaps = list(basemaps or [])
    # The refusals are ordered as a reader meets them: a value this run stated and cannot mean
    # is answered before a profile is looked up, and both before the guide on disk is counted.
    resolved_basemaps = basemaps_from_options(stated_basemaps) if stated_basemaps else list(serve_config.basemaps)
    resolved_host = host if host is not None else serve_config.host
    resolved_auth = auth if auth is not None else serve_config.auth
    preflight_auth(
        posture=resolved_auth if resolved_auth is not None else ServeAuth.NONE,
        host=resolved_host,
        live=live,
        stated=auth is not None or serve_config.auth is not None,
        jwt=serve_config.jwt,
    )
    generation = _resolved_generation(project, profile, required=live)
    if not live and not any((project.ig_directory / COMPILED_RESOURCES_RELATIVE_PATH).glob("*.json")):
        raise CompiledIgMissingError(project.config.registry_dependency)
    # The registry a guide depends on is preflighted for the reason every other refusal is: a
    # facade that starts and then serves no organisation unit is a failure nobody meets until
    # they open the picker. Both store modes read the package, so both are preflighted: a live
    # run over a registry-mode guide serves the package's units rather than building its own,
    # and a run that can reach neither source refuses here rather than publishing a second
    # identity for every place at the guide's base URL.
    resolve_registry_source(project, package=registry_package)
    return ServeInvocation(
        settings=ServeSettings(
            project_dir=project.project_root,
            publishes=project.config.ig.publishes,
            organisation_unit_geometry=(
                None
                if project.config.registry_dependency is not None
                else project.config.generate.organisation_units.geometry
            ),
            registry_package=registry_package,
            live=live,
            profile=profile,
            auth=resolved_auth if resolved_auth is not None else ServeAuth.NONE,
            auth_scope=auth_scope if auth_scope is not None else serve_config.auth_scope,
            jwt=serve_config.jwt,
            strict_codes=strict_codes if strict_codes is not None else serve_config.strict_codes,
            capture=serve_config.capture,
            ui=ui if ui is not None else serve_config.ui,
            spool_dir=serve_config.spool_dir,
            basemaps=resolved_basemaps,
            dhis2_base_url=None if generation is None else generation.profile.base_url,
            tracked_entities=serve_config.tracked_entities,
            data_sets=serve_config.data_sets,
            search=serve_config.search,
            projection=serve_config.projection,
        ),
        host=resolved_host,
        port=port if port is not None else serve_config.port,
        generation=generation,
    )
