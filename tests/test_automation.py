import threading
from pathlib import Path

import pytest

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


class _Editor:
    def __init__(self):
        self.filled = None
        self.pressed = None

    def wait_for(self, **_kwargs):
        pass

    def fill(self, value):
        self.filled = value

    def press(self, value):
        self.pressed = value


class _OriginalComposerPage:
    def __init__(self):
        self.editor = _Editor()
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        if selector != '[contenteditable="true"]':
            raise AssertionError(f"Исходный редактор не найден: {selector}")
        return self.editor


def test_text_send_uses_original_contenteditable_selector():
    page = _OriginalComposerPage()

    automation._send_text(page, "Привет 👋")

    assert page.selectors == ['[contenteditable="true"]']
    assert page.editor.filled == "Привет 👋"
    assert page.editor.pressed == "Enter"


class _LoginPage:
    def goto(self, *_args, **_kwargs):
        pass


class _LoginContext:
    def __init__(self):
        self.pages = [_LoginPage()]
        self.closed = False

    def close(self):
        self.closed = True


class _Firefox:
    def __init__(self, context):
        self.context = context

    def launch_persistent_context(self, **_kwargs):
        return self.context


class _Playwright:
    def __init__(self, context):
        self.firefox = _Firefox(context)


class _Qr:
    def __init__(self, visible):
        self.visible = visible

    def wait_for(self, **_kwargs):
        if not self.visible:
            raise automation.PlaywrightTimeoutError("QR не появился")


class _InspectionPage:
    def __init__(self, qr_visible):
        self.qr_visible = qr_visible

    def goto(self, *_args, **_kwargs):
        pass

    def locator(self, selector):
        assert selector == 'canvas[aria-label="Scan this QR code to link a device!"]'
        return _Qr(self.qr_visible)


class _InspectionContext(_LoginContext):
    def __init__(self, qr_visible):
        super().__init__()
        self.page = _InspectionPage(qr_visible)

    def new_page(self):
        return self.page


@pytest.mark.parametrize(
    ("qr_visible", "expected"),
    [(True, "needs_login"), (False, "authorized")],
)
def test_profile_check_uses_original_qr_absence_rule(tmp_path, qr_visible, expected):
    context = _InspectionContext(qr_visible)

    status, _detail = automation.inspect_profile(
        _Playwright(context),
        tmp_path / "profile",
    )

    assert status == expected
    assert context.closed is True


def test_manual_login_confirmation_marks_authorized_and_closes_browser(tmp_path):
    context = _LoginContext()
    confirmed = threading.Event()
    confirmed.set()

    status, detail = automation.authorize_profile(
        _Playwright(context),
        tmp_path / "profile",
        threading.Event(),
        confirmed,
    )

    assert (status, detail) == ("authorized", None)
    assert context.closed is True


class _Heading:
    def inner_text(self):
        return "Тестовая группа"


class _InvitePage:
    url = "https://web.whatsapp.com/accept"

    def __init__(self):
        self.closed = False

    def goto(self, *_args, **_kwargs):
        pass

    def locator(self, selector):
        assert selector == "h3"
        return _Heading()

    def close(self):
        self.closed = True


class _GroupContext:
    def __init__(self, page):
        self.page = page
        self.pages = [page]

    def new_page(self):
        return self.page


def test_group_name_and_message_send_are_logged(monkeypatch):
    page = _InvitePage()
    messages = []
    sent = []
    monkeypatch.setattr(automation, "_continue_to_web", lambda _context, page: page)
    monkeypatch.setattr(
        automation,
        "_wait_for_group_state",
        lambda _page, _stop, after_join=False: ("ready", None),
    )
    monkeypatch.setattr(
        automation,
        "_send_text",
        lambda _page, text: sent.append(text),
    )
    monkeypatch.setattr(
        automation.log,
        "info",
        lambda message, *args: messages.append(message % args),
    )

    outcome = automation.send_to_group(
        _GroupContext(page),
        "https://chat.whatsapp.com/ABCDEFGHIJKL",
        "Привет 👋",
        [],
        "caption",
        threading.Event(),
    )

    assert outcome.status == "sent"
    assert outcome.group_name == "Тестовая группа"
    assert sent == ["Привет 👋"]
    assert any("Имя группы: Тестовая группа" in message for message in messages)
    assert any("Бот уже находится в группе Тестовая группа" in message for message in messages)
    assert any("Отправляем сообщение в группу Тестовая группа" in message for message in messages)
    assert any("Сообщение отправлено в группу Тестовая группа" in message for message in messages)
