# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The retrieval thresholds that no longer exist are ignored with one DeprecationWarning, never an error."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from llmw2 import config as config_module
from llmw2.config import WikiConfig
from llmw2.settings import Settings


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_WARNED_REMOVED", set())


def test_a_removed_variable_warns_once_and_is_ignored(tmp_path: Path) -> None:
    env = {"OKF_BUNDLE": str(tmp_path), "OKF_TAU_FOLD": "0.3", "OKF_RETRIEVAL_BEAM": "4"}

    with pytest.warns(DeprecationWarning, match="crow.tau_fold"):
        cfg = WikiConfig.from_env(env)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        WikiConfig.from_env(env)  # the second read says nothing more

    assert not hasattr(cfg.crow, "tau_fold") and cfg.crow.k == 8


def test_the_settings_file_with_a_removed_key_warns(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"crow": {"tau_ret": 0.2, "k": 5}}), encoding="utf-8")

    with pytest.warns(DeprecationWarning, match="crow.tau_ret"):
        cfg = Settings({"OKF_CONFIG": str(path), "OKF_BUNDLE": str(tmp_path)}).config()

    assert cfg.crow.k == 5


def test_a_config_without_them_warns_of_nothing(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_K": "6"})
