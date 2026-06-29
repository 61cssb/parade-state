"""Unit tests for ids utility module (short_id generation)."""

from parade_state.utils import ids


class TestShortIdGeneration:
    """Test short_id generator."""

    def test_short_id_default_length(self):
        """Default short_id is 8 chars."""
        sid = ids.short_id()
        assert isinstance(sid, str)
        assert len(sid) == 8

    def test_short_id_custom_length(self):
        """Length is configurable."""
        assert len(ids.short_id(length=12)) == 12
        assert len(ids.short_id(length=4)) == 4

    def test_short_id_alphabet_is_base62(self):
        """short_id only uses characters from the base62 alphabet."""
        alphabet = set("23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
        for _ in range(200):
            sid = ids.short_id()
            assert set(sid).issubset(alphabet)
            # No ambiguous look-alikes
            assert not any(c in sid for c in "01OIl")

    def test_short_id_uniqueness_sample(self):
        """A draw of many short IDs produces negligible collisions."""
        samples = {ids.short_id() for _ in range(5000)}
        assert len(samples) == 5000  # 8-char base62: collisions vanishingly rare at this n


class TestMintUniqueShortId:
    """Test mint_unique_short_id retry logic."""

    def test_returns_unused_id(self):
        """With nothing taken, returns an 8-char id immediately."""

        def never_taken(_candidate: str) -> bool:
            return False

        sid = ids.mint_unique_short_id(never_taken)
        assert len(sid) == 8

    def test_retries_past_taken_ids(self):
        """Retries until is_taken returns False."""

        call_count = {"n": 0}

        def taken_for_first_three(candidate: str) -> bool:
            call_count["n"] += 1
            return call_count["n"] <= 3

        sid = ids.mint_unique_short_id(taken_for_first_three)
        assert len(sid) == 8
        assert call_count["n"] == 4  # 3 taken + 1 accepted

    def test_raises_when_all_attempts_collide(self):
        """Raises RuntimeError when every attempt is reported taken."""

        def always_taken(_candidate: str) -> bool:
            return True

        try:
            ids.mint_unique_short_id(always_taken, max_attempts=5)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "unique" in str(exc)

    def test_respects_custom_length(self):
        """Honours the length argument."""

        def never_taken(_candidate: str) -> bool:
            return False

        sid = ids.mint_unique_short_id(never_taken, length=10)
        assert len(sid) == 10
