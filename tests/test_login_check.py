from login_check import check_profile


def test_profile_requires_positive_authorized_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "login_check.inspect_profile",
        lambda playwright, profile: ("error", "selector changed"),
    )

    assert check_profile(object(), tmp_path) is None


def test_profile_maps_explicit_login_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "login_check.inspect_profile",
        lambda playwright, profile: ("needs_login", None),
    )

    assert check_profile(object(), tmp_path) is False
