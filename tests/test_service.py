import pytest

import script
import service as service_module
from automation import AttemptOutcome
from database import Database
from service import BotService, ConfigurationError


def configured_service(tmp_path, *, max_attempts=500):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    profile = tmp_path / "profile"
    profile.mkdir()
    database.add_account("Основной", None, profile)
    database.import_groups(
        ["https://chat.whatsapp.com/one", "https://chat.whatsapp.com/two"]
    )
    database.set_settings({"message_text": "Привет", "max_attempts": max_attempts})
    return database, BotService(database)


def test_run_refuses_to_exceed_configured_limit(tmp_path):
    _database, service = configured_service(tmp_path, max_attempts=1)

    with pytest.raises(ConfigurationError, match="безопасный лимит"):
        service.start_run()


def test_scheduled_run_is_recorded_as_skipped_while_busy(tmp_path):
    database, service = configured_service(tmp_path)
    service._operation = "Другая операция"

    run_id = service.start_run("scheduled")

    run, _ = database.get_run(run_id)
    assert run["status"] == "skipped"
    assert "Предыдущая" in run["skipped_reason"]


def test_account_limit_is_enforced(tmp_path, monkeypatch):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr("service.PROFILES_DIR", profiles)
    for index in range(10):
        profile = profiles / str(index)
        profile.mkdir()
        database.add_account(f"Аккаунт {index}", None, profile)
    service = BotService(database)

    with pytest.raises(ConfigurationError, match="не больше 10"):
        service.create_account("Лишний")


class _RunContext:
    def close(self):
        pass


class _RunFirefox:
    def launch_persistent_context(self, **_kwargs):
        return _RunContext()


class _RunPlaywright:
    firefox = _RunFirefox()


class _RunPlaywrightManager:
    def __enter__(self):
        return _RunPlaywright()

    def __exit__(self, *_args):
        pass


def test_run_worker_preserves_per_profile_kicked_statistics(tmp_path, monkeypatch):
    database, service = configured_service(tmp_path)
    monkeypatch.setattr(script, "SUMMARY_FILE", tmp_path / "stats_summary.json")
    database.set_settings({"delay_min": 0, "delay_max": 0})
    account = database.list_accounts()[0]
    groups, _ = database.list_groups(page_size=10)
    run_id = database.create_run("manual", "running", len(groups))
    outcomes = iter(
        [
            AttemptOutcome("kicked", "Первая группа"),
            AttemptOutcome("sent", "Вторая группа"),
        ]
    )
    messages = []
    monkeypatch.setattr(service_module, "sync_playwright", _RunPlaywrightManager)
    monkeypatch.setattr(service_module, "send_to_group", lambda *_args: next(outcomes))
    monkeypatch.setattr(
        script.log,
        "info",
        lambda message, *args: messages.append(message % args),
    )

    service._run_worker(
        run_id,
        [account],
        groups,
        database.get_settings(),
        [],
    )

    assert script.read_stats(account["profile_path"])["kicked_count"] == 1
    assert any("Итог по профилю" in message for message in messages)
