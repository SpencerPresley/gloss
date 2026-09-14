"""Layered, stdlib-only configuration for the command-line interface."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """A configuration file is unreadable or violates the supported schema."""


@dataclass(frozen=True)
class LoadedConfig:
    """Merged values plus enough provenance to explain their origin."""

    values: dict[str, Any]
    sources: tuple[Path, ...]
    candidates: tuple[Path, ...]
    project_root: Path | None


_SCHEMA: dict[str, object] = {
    "db": (str, type(None)),
    "ollama-url": str,
    "retrieve": {
        "k": int,
        "mode": str,
        "rerank": bool,
        "rerank-model": str,
        "rerank-prompt": (str, type(None)),
    },
    "embed": {"model": str, "batch": int},
    "build": {
        "instance": str,
        "model": str,
        "workers": int,
        "build-dir": str,
    },
    "enrich-questions": {
        "instance": str,
        "model": str,
        "workers": int,
        "build-dir": str,
    },
    "eval": {
        "cases": str,
        "k": int,
        "mode": str,
        "vs": (str, type(None)),
        "rerank": bool,
        "rerank-model": str,
        "rerank-prompt": (str, type(None)),
    },
}

_PATH_KEYS = {
    ("db",),
    ("retrieve", "rerank-prompt"),
    ("build", "instance"),
    ("build", "build-dir"),
    ("enrich-questions", "instance"),
    ("enrich-questions", "build-dir"),
    ("eval", "cases"),
    ("eval", "rerank-prompt"),
}

_CHOICES = {
    ("retrieve", "mode"): {"auto", "lexical", "hybrid", "semantic"},
    ("eval", "mode"): {"lexical", "hybrid", "semantic"},
    ("eval", "vs"): {"lexical", "hybrid", "semantic", None},
}

_POSITIVE_INTS = {
    ("retrieve", "k"),
    ("embed", "batch"),
    ("build", "workers"),
    ("enrich-questions", "workers"),
    ("eval", "k"),
}


def _home_path(value: str | Path, environ: Mapping[str, str]) -> Path:
    text = os.fspath(value)
    if text == "~":
        return Path(environ.get("HOME", Path.home()))
    if text.startswith("~/"):
        return Path(environ.get("HOME", Path.home())) / text[2:]
    return Path(text)


def _user_config_path(environ: Mapping[str, str]) -> Path:
    if environ.get("XDG_CONFIG_HOME"):
        root = _home_path(environ["XDG_CONFIG_HOME"], environ)
    else:
        root = _home_path("~/.config", environ)
    return root / "docq" / "config.json"


def _find_git_root(cwd: Path) -> Path | None:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _validate(values: object, schema: object, source: Path, prefix: tuple[str, ...] = ()) -> None:
    if isinstance(schema, dict):
        if not isinstance(values, dict):
            key = ".".join(prefix) or "<root>"
            raise ConfigError(f"configuration key '{key}' must be an object in {source}")
        for key, value in values.items():
            if key not in schema:
                dotted = ".".join((*prefix, key))
                raise ConfigError(f"unknown configuration key '{dotted}' in {source}")
            _validate(value, schema[key], source, (*prefix, key))
        return
    expected = schema if isinstance(schema, tuple) else (schema,)
    if isinstance(values, bool) and bool not in expected:
        valid = False
    else:
        valid = isinstance(values, expected)
    if not valid:
        names = " or ".join(t.__name__ for t in expected)
        raise ConfigError(
            f"configuration key '{'.'.join(prefix)}' must be {names} in {source}"
        )
    if prefix in _CHOICES and values not in _CHOICES[prefix]:
        choices = ", ".join(repr(item) for item in sorted(
            _CHOICES[prefix], key=lambda item: "" if item is None else item
        ))
        raise ConfigError(
            f"configuration key '{'.'.join(prefix)}' has unsupported value "
            f"{values!r} in {source}; choose one of {choices}"
        )
    if prefix in _POSITIVE_INTS and values < 1:
        raise ConfigError(
            f"configuration key '{'.'.join(prefix)}' must be at least 1 in {source}"
        )


def _resolve_paths(values: dict[str, Any], *, base: Path,
                   environ: Mapping[str, str], prefix: tuple[str, ...] = ()) -> None:
    for key, value in values.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            _resolve_paths(value, base=base, environ=environ, prefix=path)
        elif value is not None and path in _PATH_KEYS:
            resolved = _home_path(value, environ)
            if not resolved.is_absolute():
                resolved = base / resolved
            values[key] = str(resolved.resolve())


def _merge(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _read(path: Path, *, base: Path, environ: Mapping[str, str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read configuration {path}: {error}") from error
    _validate(value, _SCHEMA, path)
    _resolve_paths(value, base=base, environ=environ)
    return value


def load_config(*, explicit: str | Path | None = None, cwd: str | Path | None = None,
                environ: Mapping[str, str] | None = None) -> LoadedConfig:
    """Load explicit config, or merge user config then Git-root project config."""
    env = os.environ if environ is None else environ
    here = Path.cwd() if cwd is None else Path(cwd)
    root = _find_git_root(here)

    if explicit is not None:
        path = _home_path(explicit, env)
        if not path.is_absolute():
            path = here / path
        path = path.resolve()
        values = _read(path, base=path.parent, environ=env)
        return LoadedConfig(values, (path,), (path,), root)

    user = _user_config_path(env).resolve()
    project = root / ".docq" / "config.json" if root else None
    candidates = (user,) if project is None else (user, project)
    values: dict[str, Any] = {}
    sources: list[Path] = []
    if user.is_file():
        _merge(values, _read(user, base=user.parent, environ=env))
        sources.append(user)
    if project is not None and project.is_file():
        _merge(values, _read(project, base=root, environ=env))
        sources.append(project)
    return LoadedConfig(values, tuple(sources), candidates, root)
