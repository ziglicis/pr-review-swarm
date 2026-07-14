import os

from src.cli import load_dotenv


def test_load_dotenv_parses_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("STALE_KEY", "old-value")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "STALE_KEY=new-value\n"
        "QUOTED='hello'\n"
        "not a kv line\n"
    )
    load_dotenv(env)
    assert os.environ["STALE_KEY"] == "new-value"  # .env wins over inherited env
    assert os.environ["QUOTED"] == "hello"


def test_load_dotenv_missing_file_is_noop(tmp_path):
    load_dotenv(tmp_path / "nope.env")  # must not raise
