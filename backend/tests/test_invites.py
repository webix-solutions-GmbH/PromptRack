"""Invite-token crypto and the validity rule, both database-free.

Mirrors `tests/test_tokens.py`'s split: minting, hashing and the display prefix
are pure, and so — deliberately — is `is_valid`, which takes `now` as an
argument rather than reading a clock. That is what lets the four states an
invite can be in be pinned down here instead of in the integration suite, where
expiry would have to be faked by writing a timestamp into a row.
"""

from datetime import UTC, datetime, timedelta

from app.auth.invites import (
    DEFAULT_EXPIRY,
    INVITE_PREFIX,
    display_prefix,
    hash_token,
    is_valid,
    mint_invite_token,
)
from app.models import UserInvite

#: Aware, like every timestamp the app holds — the columns are `timestamptz`.
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _invite(**overrides: object) -> UserInvite:
    """An unsaved row — the model needs no session to be a value object, and
    `is_valid` needs no more than one.
    """
    fields: dict[str, object] = {
        "token_hash": "x" * 64,
        "display_prefix": "pri_abcdefg",
        "role": "member",
        "expires_at": NOW + timedelta(days=7),
        "redeemed_at": None,
        "revoked_at": None,
    }
    fields.update(overrides)
    return UserInvite(**fields)


class TestMintInviteToken:
    def test_tokens_are_unguessable_and_never_repeat(self) -> None:
        tokens = {mint_invite_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_carries_its_own_prefix_not_an_api_tokens(self) -> None:
        # A pasted secret should say what it is: `pri_` is an invite, `prk_` an
        # API token.
        raw = mint_invite_token()
        assert raw.startswith(INVITE_PREFIX)
        assert not raw.startswith("prk_")

    def test_body_is_32_bytes_urlsafe_encoded(self) -> None:
        # 32 random bytes, base64url without padding, is 43 characters.
        assert len(mint_invite_token()) == len(INVITE_PREFIX) + 43


class TestHashToken:
    def test_the_stored_hash_is_not_the_token(self) -> None:
        raw = mint_invite_token()
        assert hash_token(raw) != raw

    def test_hashing_is_deterministic(self) -> None:
        # Unlike a password hash: the lookup is by hash, so it has to be.
        raw = mint_invite_token()
        assert hash_token(raw) == hash_token(raw)
        assert hash_token(raw) != hash_token(mint_invite_token())

    def test_trims_surrounding_whitespace(self) -> None:
        # A link pasted out of a mail client often carries one.
        raw = mint_invite_token()
        assert hash_token(raw) == hash_token(f"  {raw}\n")


class TestDisplayPrefix:
    def test_is_a_strict_prefix_of_the_raw_token(self) -> None:
        raw = mint_invite_token()
        prefix = display_prefix(raw)
        assert raw.startswith(prefix)
        assert len(prefix) == 12

    def test_alone_is_not_enough_to_reconstruct_the_token(self) -> None:
        raw = mint_invite_token()
        assert display_prefix(raw) != raw


class TestIsValid:
    def test_a_fresh_untouched_invite_is_valid(self) -> None:
        assert is_valid(_invite(), NOW)

    def test_an_expired_invite_is_not(self) -> None:
        assert not is_valid(_invite(expires_at=NOW - timedelta(seconds=1)), NOW)

    def test_expiry_is_exclusive_at_the_boundary(self) -> None:
        # `expires_at > now`: the instant it expires, it has expired.
        assert not is_valid(_invite(expires_at=NOW), NOW)

    def test_a_redeemed_invite_is_not(self) -> None:
        # Single use is the whole point — one link lets in one person.
        assert not is_valid(_invite(redeemed_at=NOW - timedelta(days=1)), NOW)

    def test_a_revoked_invite_is_not(self) -> None:
        assert not is_valid(_invite(revoked_at=NOW - timedelta(days=1)), NOW)

    def test_the_default_expiry_is_a_week(self) -> None:
        # A link that lets a stranger create an account should not sit in an
        # inbox for a quarter.
        assert DEFAULT_EXPIRY == timedelta(days=7)
