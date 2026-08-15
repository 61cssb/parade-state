"""Unit tests for ids utility module (UUID generation/validation/conversion)."""

import uuid as uuid_module

import pytest

from parade_state.utils import ids


class TestUuidGeneration:
    """Test UUID generators."""

    def test_uuid4_returns_uuid_object(self):
        result = ids.uuid4()
        assert isinstance(result, uuid_module.UUID)
        assert result.version == 4

    def test_uuid4_unique(self):
        assert ids.uuid4() != ids.uuid4()

    def test_uuid4_str_returns_string(self):
        result = ids.uuid4_str()
        assert isinstance(result, str)
        assert len(result) == 36  # canonical 8-4-4-4-12 format

    def test_db_default_returns_uuid_string(self):
        result = ids.db_default()
        assert isinstance(result, str)
        assert ids.is_valid(result)


class TestUuidValidation:
    """Test UUID validation."""

    def test_is_valid_accepts_valid_uuid(self):
        assert ids.is_valid("12345678-1234-5678-1234-567812345678") is True

    def test_is_valid_rejects_invalid_string(self):
        assert ids.is_valid("invalid-uuid") is False

    def test_is_valid_rejects_non_string(self):
        assert ids.is_valid(12345) is False

    def test_validate_valid_uuid_no_error(self):
        ids.validate("12345678-1234-5678-1234-567812345678")  # no raise

    def test_validate_invalid_uuid_raises(self):
        with pytest.raises(ValueError):
            ids.validate("invalid-uuid")

    def test_validate_non_string_raises_type_error(self):
        with pytest.raises(TypeError):
            ids.validate(None)


class TestUuidConversion:
    """Test UUID conversion helpers."""

    def test_to_uuid_from_string(self):
        uuid_obj = ids.to_uuid("12345678-1234-5678-1234-567812345678")
        assert isinstance(uuid_obj, uuid_module.UUID)

    def test_to_uuid_passthrough_uuid_object(self):
        original = uuid_module.uuid4()
        assert ids.to_uuid(original) is original

    def test_to_uuid_invalid_string_raises(self):
        with pytest.raises(ValueError):
            ids.to_uuid("invalid")

    def test_to_uuid_non_string_raises(self):
        with pytest.raises(TypeError):
            ids.to_uuid(123)

    def test_to_string_from_uuid_object(self):
        result = ids.to_string(uuid_module.UUID("12345678-1234-5678-1234-567812345678"))
        assert result == "12345678-1234-5678-1234-567812345678"

    def test_to_string_valid_string_passthrough(self):
        assert ids.to_string("12345678-1234-5678-1234-567812345678") == (
            "12345678-1234-5678-1234-567812345678"
        )

    def test_to_string_invalid_string_raises(self):
        with pytest.raises(ValueError):
            ids.to_string("not-a-uuid")

    def test_or_default_returns_value_when_valid(self):
        assert ids.or_default("12345678-1234-5678-1234-567812345678") == (
            "12345678-1234-5678-1234-567812345678"
        )

    def test_or_default_returns_default_when_none(self):
        assert ids.or_default(None, "fallback") == "fallback"

    def test_or_default_returns_default_when_invalid(self):
        assert ids.or_default("invalid", "fallback") == "fallback"
