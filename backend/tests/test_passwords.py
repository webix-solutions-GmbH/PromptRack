"""Credential handling that needs no database: argon2 and the session token.

Kept small on purpose — argon2 is deliberately slow, so this suite hashes a
handful of times and no more. The wired-up half (a cookie that actually signs a
request in) is `tests/integration/test_auth_flow.py`.
"""

from app.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.auth.sessions import hash_token, mint_session_token

PASSWORD = "correct horse battery staple"


class TestHashPassword:
    def test_uses_argon2id(self) -> None:
        # The encoded hash names its own algorithm and parameters, which is why
        # nothing in this app pins cost factors in code.
        assert hash_password(PASSWORD).startswith("$argon2id$")

    def test_is_salted(self) -> None:
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_has_a_minimum_length_the_request_schema_can_enforce(self) -> None:
        assert MIN_PASSWORD_LENGTH >= 12


class TestVerifyPassword:
    def test_accepts_the_password_it_hashed(self) -> None:
        assert verify_password(hash_password(PASSWORD), PASSWORD)

    def test_rejects_a_wrong_password(self) -> None:
        assert not verify_password(hash_password(PASSWORD), PASSWORD + "!")

    def test_rejects_an_account_with_no_password(self) -> None:
        # An OIDC-only account must not be signable-into with any password, and
        # the check still pays for one verification so the timing does not say
        # which kind of account it is.
        assert not verify_password(None, PASSWORD)

    def test_treats_a_broken_stored_hash_as_a_refusal(self) -> None:
        # A corrupt row is a failed login, not a 500.
        assert not verify_password("not-a-hash", PASSWORD)

    def test_a_fresh_hash_needs_no_rehash(self) -> None:
        assert not needs_rehash(hash_password(PASSWORD))


class TestSessionTokens:
    def test_tokens_are_unguessable_and_never_repeat(self) -> None:
        tokens = {mint_session_token() for _ in range(100)}
        assert len(tokens) == 100
        # 32 random bytes, urlsafe-encoded.
        assert all(len(token) >= 43 for token in tokens)

    def test_the_stored_hash_is_not_the_token(self) -> None:
        raw = mint_session_token()
        assert hash_token(raw) != raw

    def test_hashing_is_deterministic(self) -> None:
        # Unlike a password hash: the lookup is by hash, so it has to be.
        raw = mint_session_token()
        assert hash_token(raw) == hash_token(raw)
        assert hash_token(raw) != hash_token(mint_session_token())
