from pathlib import Path

import automation


def test_invite_url_is_redacted():
    value = automation.redact_invite_url(
        "https://chat.whatsapp.com/ABCDEFGHIJKL?secret=value"
    )

    assert value == "https://chat.whatsapp.com/ABCD…IJKL"
    assert "secret" not in value


def test_images_are_split_into_batches_and_caption_is_not_duplicated(monkeypatch):
    calls = []
    monkeypatch.setattr(
        automation,
        "_send_image_batch",
        lambda page, paths, caption: calls.append((paths, caption)),
    )
    paths = [Path(f"{index}.jpg") for index in range(65)]

    automation._send_images(object(), paths, "Подпись")

    assert [len(paths) for paths, _caption in calls] == [30, 30, 5]
    assert [caption for _paths, caption in calls] == ["Подпись", "", ""]
