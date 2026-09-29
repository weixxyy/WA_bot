from database import Database


def make_database(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    return database


def test_defaults_and_group_import(tmp_path):
    database = make_database(tmp_path)
    assert database.get_settings()["max_attempts"] == 500

    added, duplicates = database.import_groups(
        ["https://chat.whatsapp.com/one", "https://chat.whatsapp.com/two"]
    )
    assert (added, duplicates) == (2, 0)
    assert database.import_groups(["https://chat.whatsapp.com/one"]) == (0, 1)

    groups, total = database.list_groups(page_size=100)
    assert total == 2
    assert {group["invite_url"] for group in groups} == {
        "https://chat.whatsapp.com/one",
        "https://chat.whatsapp.com/two",
    }


def test_run_and_attempt_counters(tmp_path):
    database = make_database(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    account_id = database.add_account("Основной", None, profile)
    database.import_groups(["https://chat.whatsapp.com/one"])
    account = database.get_account(account_id)
    groups, _ = database.list_groups(page_size=10)

    run_id = database.create_run("manual", "running", 1)
    attempt_id = database.add_attempt(run_id, account, groups[0])
    database.finish_attempt(attempt_id, "sent")
    database.finish_run(run_id, "completed")

    run, attempts = database.get_run(run_id)
    assert run["completed_attempts"] == 1
    assert run["sent_count"] == 1
    assert run["error_count"] == 0
    assert attempts[0]["status"] == "sent"


def test_initialize_marks_crashed_work_as_interrupted(tmp_path):
    database = make_database(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    account_id = database.add_account("Основной", None, profile)
    database.import_groups(["https://chat.whatsapp.com/one"])
    account = database.get_account(account_id)
    groups, _ = database.list_groups(page_size=10)
    run_id = database.create_run("manual", "running", 1)
    database.add_attempt(run_id, account, groups[0])

    database.initialize()

    run, attempts = database.get_run(run_id)
    assert run["status"] == "interrupted"
    assert attempts[0]["status"] == "interrupted"
