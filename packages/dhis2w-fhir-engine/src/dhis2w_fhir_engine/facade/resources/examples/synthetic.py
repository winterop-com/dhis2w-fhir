"""Seeded values for synthetic captures: the digest behind every draw, DHIS2-shaped UIDs, and constrained values."""

from __future__ import annotations

import hashlib
import random
import string

#: The value types a synthesised answer leaves unanswered. An attachment or a geometry blob says
#: nothing useful when it is invented, and inventing one would misrepresent the form. `REFERENCE`
#: and `TRACKER_ASSOCIATE` point at DHIS2 objects the IG publishes no FHIR resource for, so an
#: answer would name a target no consumer can resolve - and DHIS2 refuses an invented one with
#: `E1302`. Every synthesising emitter reads this set, so a question of one of these types is
#: unanswered in the guide's example corpus and in a `$generate` response alike.
UNSYNTHESIZABLE_VALUE_TYPES = frozenset({"FILE_RESOURCE", "IMAGE", "GEOJSON", "REFERENCE", "TRACKER_ASSOCIATE"})

#: The character a DHIS2 UID opens on, and the characters its remaining ten places take.
_UID_LEADING_CHARACTERS = string.ascii_letters

_UID_TRAILING_CHARACTERS = string.ascii_letters + string.digits

#: How many places follow the leading letter of a DHIS2 UID.
_UID_TRAILING_LENGTH = 10

#: Bytes of the seed digest fed to the synthetic RNG - eight is a full 64-bit seed.
_SEED_BYTES = 8


def derived_seed(material: str, ordinal: int, salt: str = "") -> int:
    """The RNG seed for one target's `n`-th example: the leading 64 bits of a SHA-256 digest.

    `salt` moves every draw of a run at once, which is what mints a *second* corpus rather than a
    second copy of the first. An empty salt - the default, and every examples build - hashes the
    same material it always did, so an unsalted run stays byte-identical.
    """
    keyed_material = f"{material}:{ordinal}:{salt}" if salt else f"{material}:{ordinal}"
    digest = hashlib.sha256(keyed_material.encode()).digest()
    return int.from_bytes(digest[:_SEED_BYTES], "big")


def _token_number(token: str, digits: int) -> int:
    """A deterministic non-negative number of at most `digits` places, derived from one identity token."""
    drawn = int.from_bytes(hashlib.sha256(token.encode()).digest()[:_SEED_BYTES], "big")
    modulus: int = 10**digits
    return drawn % modulus


def distinct_unique_value(name: str, value_type: str, token: str) -> str | None:
    """A corpus-distinct value for a unique attribute, or None when its value type admits none.

    DHIS2 refuses a second tracked entity carrying a `unique` attribute's value with `E1064`, so a
    corpus answering one question with one invented constant registers exactly one person and
    cascades every enrollment and event behind it into failure. The value therefore carries the
    response's own minted tracked-entity UID - the one thing no other response in the corpus holds -
    spelled the way the attribute's value type admits.

    A value type with no room for it answers None and is tallied rather than faked: an option-bound
    attribute has only as many values as its option set holds, a `LETTER` has 52, and a date or a
    boolean has fewer still. Inventing a value outside what the type admits to dodge a duplicate
    would trade a refusal we can explain for one we cannot.
    """
    if value_type in _UNIQUE_TEXT_VALUE_TYPES:
        return f"{name} {token}"
    if value_type == _EMAIL_VALUE_TYPE:
        return f"{token.lower()}@{_SYNTHETIC_EMAIL_DOMAIN}"
    if value_type == _PHONE_NUMBER_VALUE_TYPE:
        return f"+{_token_number(token, _UNIQUE_PHONE_DIGITS):0{_UNIQUE_PHONE_DIGITS}d}"
    if value_type == URI_VALUE_TYPE:
        return f"{_SYNTHETIC_URL_HOST}/{token}"
    if value_type in INTEGER_VALUE_TYPES:
        return str(_distinct_integer(value_type, token))
    return None


def synthetic_uid(generator: random.Random) -> str:
    """A seeded DHIS2-shaped UID: one ASCII letter followed by ten alphanumeric places."""
    leading = generator.choice(_UID_LEADING_CHARACTERS)
    trailing = "".join(generator.choice(_UID_TRAILING_CHARACTERS) for _ in range(_UID_TRAILING_LENGTH))
    return f"{leading}{trailing}"


def seeded_format_constrained_value(value_type: str, generator: random.Random) -> str | None:
    """One seeded value for a DHIS2 value type whose stored value has a shape, or None for free text.

    DHIS2 parses these types rather than storing what it is handed: it refuses `E1302` for a
    `COORDINATE` that is not a longitude and latitude pair, an `EMAIL` that is not an address, a
    `LETTER` that is a word, a `PHONE_NUMBER` carrying letters, and a `USERNAME` with a space in
    it. A free-text type answers None and the caller's own wording stands, because free text is
    exactly what DHIS2 stores for it.

    Every synthesising emitter draws from here - the guide's example corpus and the server's
    `$generate` alike - so one value type is spelled one way across the project, and a value type
    whose shape DHIS2 tightens is answered in one place. The draw is the caller's own seeded
    generator, so the same seed keeps producing the same value.
    """
    if value_type == _COORDINATE_VALUE_TYPE:
        return _seeded_coordinate(generator)
    if value_type == _EMAIL_VALUE_TYPE:
        return _seeded_email(generator)
    if value_type == _PHONE_NUMBER_VALUE_TYPE:
        return _seeded_phone_number(generator)
    if value_type == _LETTER_VALUE_TYPE:
        return _seeded_letter(generator)
    if value_type == _USERNAME_VALUE_TYPE:
        return _seeded_username(generator)
    return None


#: DHIS2 value types answered as a FHIR integer.
INTEGER_VALUE_TYPES = frozenset({"INTEGER", "INTEGER_POSITIVE", "INTEGER_NEGATIVE", "INTEGER_ZERO_OR_POSITIVE"})

#: The DHIS2 value type answered as a FHIR uri.
URI_VALUE_TYPE = "URL"

#: The host a synthetic URL points at - `.invalid` is reserved by RFC 2606 and resolves nowhere.
_SYNTHETIC_URL_HOST = "https://example.invalid"

#: The domain a synthetic EMAIL answer lands in. RFC 2606 reserves `example.com` for documentation
#: exactly as it reserves `.invalid`, and the two differ on the one axis that decides an import:
#: DHIS2 grades an EMAIL against its top-level domain and answers `E1007` to an address at
#: `.invalid`, so a seeded address lands at the reserved domain DHIS2 accepts. A URL is graded as a
#: URI rather than as an address, so `_SYNTHETIC_URL_HOST` stays at `.invalid` and resolves nowhere.
_SYNTHETIC_EMAIL_DOMAIN = "example.com"

#: The DHIS2 value types a *unique* attribute's value can be spelled corpus-distinct in by carrying
#: the response's own minted identity: a free-text field takes it verbatim, and the three shaped
#: textual types take it in their own shape. Everything else is drawn as usual and tallied.
_UNIQUE_TEXT_VALUE_TYPES = frozenset({"TEXT", "LONG_TEXT", "USERNAME"})

_EMAIL_VALUE_TYPE = "EMAIL"

_PHONE_NUMBER_VALUE_TYPE = "PHONE_NUMBER"

#: The remaining DHIS2 value types whose stored value has a shape DHIS2 parses rather than keeps
#: verbatim, so a synthesised answer has to be spelled in it. `COORDINATE` is the `[lon,lat]`
#: string DHIS2 stores for a point, `LETTER` is a single letter, and `USERNAME` names an account.
_COORDINATE_VALUE_TYPE = "COORDINATE"

_LETTER_VALUE_TYPE = "LETTER"

_USERNAME_VALUE_TYPE = "USERNAME"

#: How many digits a distinct `PHONE_NUMBER` carries after its leading `+`.
_UNIQUE_PHONE_DIGITS = 11


def _distinct_integer(value_type: str, token: str) -> int:
    """One distinct whole number for a unique attribute, on whichever side of zero its value type admits."""
    drawn = _token_number(token, _UNIQUE_NUMBER_DIGITS)
    if value_type == "INTEGER_NEGATIVE":
        return -(drawn + 1)
    if value_type == "INTEGER_POSITIVE":
        return drawn + 1
    return drawn


def _seeded_coordinate(generator: random.Random) -> str:
    """A seeded `[longitude,latitude]` pair in the DHIS2 COORDINATE wire spelling."""
    places = _SYNTHETIC_COORDINATE_PLACES
    longitude = round(generator.uniform(-_SYNTHETIC_LONGITUDE_BOUND, _SYNTHETIC_LONGITUDE_BOUND), places)
    latitude = round(generator.uniform(-_SYNTHETIC_LATITUDE_BOUND, _SYNTHETIC_LATITUDE_BOUND), places)
    return f"[{longitude},{latitude}]"


def _seeded_email(generator: random.Random) -> str:
    """A seeded address at the RFC 2606 domain DHIS2 imports, which is what an EMAIL question stores."""
    return f"{_seeded_account_name(generator)}@{_SYNTHETIC_EMAIL_DOMAIN}"


def _seeded_phone_number(generator: random.Random) -> str:
    """A seeded phone number in the plus-then-digits spelling DHIS2 admits for PHONE_NUMBER."""
    return f"+{generator.randrange(10**_SEEDED_PHONE_DIGITS):0{_SEEDED_PHONE_DIGITS}d}"


def _seeded_letter(generator: random.Random) -> str:
    """A seeded single letter - the whole of what DHIS2 stores behind a LETTER question."""
    return generator.choice(string.ascii_uppercase)


def _seeded_username(generator: random.Random) -> str:
    """A seeded account name, which is what a USERNAME question names."""
    return _seeded_account_name(generator)


#: How many digits a seeded `PHONE_NUMBER` carries after its leading `+`.
_SEEDED_PHONE_DIGITS = 11

#: How many digits a distinct numeric value for a unique attribute is drawn across. Nine keeps every
#: value inside the 32-bit signed range DHIS2 stores an `INTEGER` in, while leaving a billion of them.
_UNIQUE_NUMBER_DIGITS = 9

#: The bounds and precision of a synthetic `COORDINATE` answer's `[longitude,latitude]` pair.
_SYNTHETIC_LONGITUDE_BOUND = 180.0

_SYNTHETIC_LATITUDE_BOUND = 90.0

_SYNTHETIC_COORDINATE_PLACES = 4


def _seeded_account_name(generator: random.Random) -> str:
    """A seeded letters-then-digits account name, used as a username and as an email local part."""
    return f"{_SEEDED_ACCOUNT_PREFIX}{generator.randrange(10**_SEEDED_ACCOUNT_DIGITS):0{_SEEDED_ACCOUNT_DIGITS}d}"


#: What a seeded account name opens with, and how many digits distinguish one from the next. Four
#: letters plus six digits clears the four-character minimum DHIS2 holds a username to.
_SEEDED_ACCOUNT_PREFIX = "user"

_SEEDED_ACCOUNT_DIGITS = 6
