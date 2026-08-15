"""Password hashing — argon2id, with the library's own defaults.

argon2id is the only algorithm here (it is `argon2-cffi`'s default `type`), and
the cost parameters are deliberately not pinned to hand-picked numbers: they
travel *inside* the hash string, so raising them later is a one-line change plus
:func:`needs_rehash` on the next successful login, and the library's defaults
track the current RFC 9106 recommendation better than a number frozen in this
file would.

This is also the reason API tokens are **not** hashed here (`app.auth.tokens`
uses SHA-256): a 256-bit random secret has nothing to brute-force, and every
MCP request would otherwise pay an argon2.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: Refused shorter, in the request schema. Long-and-simple beats
#: short-and-clever.
MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()

#: Verified against when an account has no password at all (OIDC-only), so a
#: sign-in attempt costs the same either way and cannot be timed to learn
#: whether an address exists or how it authenticates.
_DUMMY_HASH = _hasher.hash("promptrack-dummy-password")


def hash_password(raw: str) -> str:
    """The encoded argon2id hash — algorithm, parameters and salt included."""
    return _hasher.hash(raw)


def verify_password(stored_hash: str | None, raw: str) -> bool:
    """Whether ``raw`` is the password behind ``stored_hash``.

    ``None`` (an account that only signs in through OIDC) is a refusal, but a
    refusal that still pays for one verification — see :data:`_DUMMY_HASH`.
    A malformed stored hash is also just ``False``: a broken row must not turn
    a failed login into a 500.
    """
    try:
        _hasher.verify(stored_hash if stored_hash is not None else _DUMMY_HASH, raw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """Whether the hash was made with weaker parameters than today's defaults.

    Only answerable right after a successful verification, which is the one
    moment the plaintext is at hand to re-hash it.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False
