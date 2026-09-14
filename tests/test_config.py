from __future__ import annotations

import json
from pathlib import Path

import pytest

from docq.config import ConfigError, load_config


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_user_and_project_config_merge_with_project_precedence(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src" / "feature"
    nested.mkdir(parents=True)

    _write(home / ".config" / "docq" / "config.json", {
        "db": "~/corpora/personal.db",
        "retrieve": {"mode": "lexical", "rerank-model": "user-model"},
    })
    _write(repo / ".docq" / "config.json", {
        "db": "data/work.db",
        "retrieve": {"mode": "hybrid"},
    })

    loaded = load_config(cwd=nested, environ={"HOME": str(home)})

    assert loaded.values["db"] == str(repo / "data" / "work.db")
    assert loaded.values["retrieve"] == {
        "mode": "hybrid",
        "rerank-model": "user-model",
    }
    assert loaded.sources == (
        home / ".config" / "docq" / "config.json",
        repo / ".docq" / "config.json",
    )


def test_explicit_config_disables_automatic_discovery(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _write(home / ".config" / "docq" / "config.json", {"db": "user.db"})
    _write(repo / ".docq" / "config.json", {"db": "project.db"})
    explicit = tmp_path / "elsewhere" / "chosen.json"
    _write(explicit, {"db": "chosen.db"})

    loaded = load_config(explicit=explicit, cwd=repo, environ={"HOME": str(home)})

    assert loaded.values["db"] == str(explicit.parent / "chosen.db")
    assert loaded.sources == (explicit,)


def test_unknown_config_key_names_file_and_key(tmp_path):
    config = tmp_path / "config.json"
    _write(config, {"retrieve": {"rerank-modle": "typo"}})

    with pytest.raises(ConfigError, match=r"rerank-modle.*config\.json"):
        load_config(explicit=config)


def test_invalid_choice_is_rejected_before_argument_parsing(tmp_path):
    config = tmp_path / "config.json"
    _write(config, {"retrieve": {"mode": "sometimes"}})

    with pytest.raises(ConfigError, match=r"retrieve\.mode.*sometimes"):
        load_config(explicit=config)
