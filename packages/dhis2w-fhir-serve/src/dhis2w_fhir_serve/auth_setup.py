"""What a run checks about its authentication posture before it starts, and the issuer it reads while starting."""

from __future__ import annotations

import ipaddress

from dhis2w_fhir_engine.facade.api.auth import SERVE_TOKENS_VARIABLE, read_serve_tokens
from dhis2w_fhir_engine.facade.config import ServeAuth, ServeJwtConfig

from dhis2w_fhir_serve.oidc import JwtVerifier, OidcIssuerUnavailableError, discover_issuer


class ServeAuthConfigurationError(ValueError):
    """A posture this run cannot honour, refused while the settings resolve rather than at a request.

    A `ValueError` because that is what `ServeSettings.resolve` already raises for a stated value it
    cannot mean, and `d2w fhir serve` renders it as one line against the dial it came from.
    """


async def open_jwt_verifier(config: ServeJwtConfig) -> JwtVerifier:
    """Read the issuer this run trusts, while the server starts, or refuse to serve at all.

    The refusal is a `ServeAuthConfigurationError` so it lands beside the other posture refusals - a
    deployer meets one line naming the key that has to change, whether the problem was an issuer
    nobody wrote down or one nobody could reach.
    """
    try:
        return await discover_issuer(config)
    except OidcIssuerUnavailableError as error:
        raise ServeAuthConfigurationError(
            f'`auth = "jwt"` verifies every caller\'s token against the keys `{config.issuer}` publishes, '
            f"and this server could not read them: {error}. Check `[serve.jwt] issuer` names the issuer "
            "identifier its own tokens carry as `iss`, and that this machine can reach it."
        ) from error


def preflight_auth(
    *,
    posture: ServeAuth,
    host: str,
    live: bool,
    stated: bool,
    jwt: ServeJwtConfig | None = None,
    environment: dict[str, str] | None = None,
) -> None:
    """Refuse, before anything binds, every posture this run could not actually honour.

    Five refusals, each about a run rather than about a request, which is why they are here and not
    in the dependency. A server that starts and then refuses every caller - or worse, serves every
    caller from an interface the deployment thought was closed - is a failure nobody reads until it
    matters.

    Two of them are the `jwt` posture's: an issuer nobody named, and `forward_bearer` asked for on a
    run with no instance to forward to. The third thing that posture needs - an issuer this machine
    can actually reach - is not checked here, because reading it is a round trip and this function is
    a reader of values. `dhis2w_fhir_engine.facade.api.auth.open_jwt_verifier` does it while the server starts
    and refuses with the same error type.
    """
    if not stated and not _is_loopback(host):
        raise ServeAuthConfigurationError(
            f"`{host}` is not a loopback interface, and this project's fhir.toml states no `[serve] auth`. "
            "Write the posture down before serving the facade where other hosts can reach it - add one "
            'line under `[serve]` in fhir.toml: `auth = "none"` to serve every caller, `auth = "token"` '
            f'to take a static bearer token out of `{SERVE_TOKENS_VARIABLE}`, `auth = "dhis2"` to have '
            "every caller present the DHIS2 credentials this facade checks against the instance, or "
            '`auth = "jwt"` to take a token from an OpenID Connect issuer named in `[serve.jwt] issuer`. '
            "`--auth` states the same thing for one run."
        )
    if posture is ServeAuth.TOKEN and not read_serve_tokens(environment):
        raise ServeAuthConfigurationError(
            f'`auth = "token"` takes its tokens from the environment variable `{SERVE_TOKENS_VARIABLE}`, '
            "which is unset or empty. Set it to the tokens this deployment accepts, comma-separated, and "
            "serve again. The tokens are secrets and do not belong in fhir.toml, which is a file projects "
            "commit; rotating them is replacing the variable and restarting this process."
        )
    if posture is ServeAuth.DHIS2 and not live:
        raise ServeAuthConfigurationError(
            '`auth = "dhis2"` checks every caller\'s credentials against the DHIS2 instance this run reads, '
            "and this run reads a compiled implementation guide off disk instead - there is no instance to "
            'check anybody against. Serve with `--live`, or state `auth = "token"` or `auth = "none"`.'
        )
    if posture is ServeAuth.JWT:
        _preflight_jwt(jwt if jwt is not None else ServeJwtConfig(), live=live)


def _preflight_jwt(jwt: ServeJwtConfig, *, live: bool) -> None:
    """The two things `auth = "jwt"` needs stated before a socket opens: an issuer, and a place to forward to."""
    if jwt.issuer is None:
        raise ServeAuthConfigurationError(
            '`auth = "jwt"` verifies every caller\'s token against the keys one OpenID Connect issuer '
            "publishes, and this project's fhir.toml names no `[serve.jwt] issuer`. Add the table and the "
            'key - `[serve.jwt]` then `issuer = "https://idp.example.org/realms/health"` - naming the '
            "issuer identifier its own tokens carry as `iss`. This server appends "
            "`/.well-known/openid-configuration` to it and takes the keys from there."
        )
    if jwt.forward_bearer and not live:
        raise ServeAuthConfigurationError(
            "`[serve.jwt] forward_bearer = true` sends each caller's own token on to the DHIS2 instance "
            "this run reads, and this run reads a compiled implementation guide off disk instead - there "
            "is no instance to send anything to. Serve with `--live`, or set `forward_bearer = false`."
        )


def _is_loopback(host: str) -> bool:
    """Whether an address the process would bind only answers this machine.

    A name rather than an address is not loopback here, `localhost` excepted. Resolving a name would
    make the refusal depend on the machine's resolver, and a name that resolves to a loopback address
    today is a deployment decision somebody should still have written down.
    """
    stripped = host.strip().strip("[]")
    if stripped.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(stripped).is_loopback
    except ValueError:
        return False
