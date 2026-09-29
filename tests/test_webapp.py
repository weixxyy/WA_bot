import re

from fastapi.testclient import TestClient

from database import Database
from webapp import create_app


class DummyService:
    def __init__(self):
        self.confirmed_account_id = None

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {
            "operation": None,
            "running": False,
            "enabled_accounts": 0,
            "enabled_groups": 0,
            "planned_attempts": 0,
            "next_run": None,
            "timezone": "Europe/Moscow",
        }

    def notify_schedule_changed(self):
        pass

    def confirm_account_authorized(self, account_id):
        self.confirmed_account_id = account_id


def test_dashboard_and_bulk_group_import(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    app = create_app(database=database, service=DummyService())

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "WA Bot" in response.text
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

        response = client.post(
            "/groups/import",
            data={
                "csrf_token": token,
                "links": "текст https://chat.whatsapp.com/AbCd1234 и ещё текст",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        response = client.get("/groups")
        assert "https://chat.whatsapp.com/AbCd1234" in response.text


def test_post_requires_csrf(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    app = create_app(database=database, service=DummyService())

    with TestClient(app) as client:
        response = client.post("/message", data={"message_text": "test"})
        assert response.status_code == 403


def test_account_login_can_be_confirmed_from_panel(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    profile = tmp_path / "profile"
    profile.mkdir()
    account_id = database.add_account("Основной", None, profile)
    service = DummyService()
    app = create_app(database=database, service=service)
    database.update_account(account_id, status="authorizing")

    with TestClient(app) as client:
        response = client.get("/accounts")
        assert "Вход выполнен — закрыть окно" in response.text
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        response = client.post(
            f"/accounts/{account_id}/confirm-authorized",
            data={"csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert service.confirmed_account_id == account_id
