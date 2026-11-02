from app.main import main


def test_main_starts_successfully(monkeypatch) -> None:
    monkeypatch.setenv("TASKLOOM_ENVIRONMENT", "test")

    assert main() == 0
