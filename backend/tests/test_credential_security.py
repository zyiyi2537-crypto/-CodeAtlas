from __future__ import annotations

import pytest

from codeatlas.security import mask_credential_ref, validate_credential_ref


def test_credential_ref_rejects_secret_like_value() -> None:
    with pytest.raises(ValueError, match="reference"):
        validate_credential_ref("sk-live-secret-value-1234567890")


def test_credential_ref_masks_any_display_value() -> None:
    assert mask_credential_ref("embedding-company") == "已配置"
    assert mask_credential_ref("") == "未配置"
