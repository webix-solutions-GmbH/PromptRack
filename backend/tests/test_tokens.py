"""API-token crypto and header parsing that need no database.

Mirrors `tests/test_passwords.py`'s split: the pure half of tokens lives here
(minting, hashing, the display prefix, and `presented_token`'s header
precedence); `resolve_token` itself needs a database and is exercised in the
integration suite instead.
"""

from starlette.datastructures import Headers

from app.auth.guards import presented_token
from app.auth.tokens import TOKEN_PREFIX, display_prefix, hash_token, mint_token


class TestMintToken:
    def test_tokens_are_unguessable_and_never_repeat(self) -> None:
        tokens = {mint_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_carries_the_product_prefix(self) -> None:
        assert mint_token().startswith(TOKEN_PREFIX)

    def test_body_is_32_bytes_urlsafe_encoded(self) -> None:
        # 32 random bytes, base64url without padding, is 43 characters.
        raw = mint_token()
        assert len(raw) == len(TOKEN_PREFIX) + 43


class TestHashToken:
    def test_the_stored_hash_is_not_the_token(self) -> None:
        raw = mint_token()
        assert hash_token(raw) != raw

    def test_hashing_is_deterministic(self) -> None:
        # Unlike a password hash: the lookup is by hash, so it has to be.
        raw = mint_token()
        assert hash_token(raw) == hash_token(raw)
        assert hash_token(raw) != hash_token(mint_token())

    def test_trims_surrounding_whitespace(self) -> None:
        # A token pasted from a terminal or a form field often carries one.
        raw = mint_token()
        assert hash_token(raw) == hash_token(f"  {raw}\n")


class TestDisplayPrefix:
    def test_is_a_strict_prefix_of_the_raw_token(self) -> None:
        raw = mint_token()
        prefix = display_prefix(raw)
        assert raw.startswith(prefix)
        assert len(prefix) == 12

    def test_alone_is_not_enough_to_reconstruct_the_token(self) -> None:
        raw = mint_token()
        assert display_prefix(raw) != raw


class TestPresentedToken:
    def test_reads_the_api_key_header(self) -> None:
        headers = Headers({"x-api-key": "prk_abc"})
        assert presented_token(headers) == "prk_abc"

    def test_falls_back_to_a_bearer_authorization_header(self) -> None:
        headers = Headers({"authorization": "Bearer prk_abc"})
        assert presented_token(headers) == "prk_abc"

    def test_authorization_matching_is_case_insensitive_on_the_scheme(self) -> None:
        headers = Headers({"authorization": "bearer prk_abc"})
        assert presented_token(headers) == "prk_abc"

    def test_api_key_wins_when_both_are_present(self) -> None:
        # So a reverse proxy's own HTTP basic auth can occupy `Authorization`
        # without shadowing an MCP client's token in `x-api-key`.
        headers = Headers({"x-api-key": "prk_direct", "authorization": "Basic dXNlcjpwYXNz"})
        assert presented_token(headers) == "prk_direct"

    def test_a_non_bearer_authorization_header_is_not_a_token(self) -> None:
        headers = Headers({"authorization": "Basic dXNlcjpwYXNz"})
        assert presented_token(headers) is None

    def test_neither_header_present_is_none(self) -> None:
        assert presented_token(Headers({})) is None

    def test_blank_api_key_header_falls_through(self) -> None:
        headers = Headers({"x-api-key": "   ", "authorization": "Bearer prk_abc"})
        assert presented_token(headers) == "prk_abc"
