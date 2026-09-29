import re

from fastapi.testclient import TestClient

from database import Database
from webapp import create_app


class DummyService:
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
