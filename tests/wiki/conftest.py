# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from pathlib import Path

import pytest

from okf_wiki import Wiki, WikiConfig
from okf_wiki.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM


@pytest.fixture
def tracker() -> UsageTracker:
    return UsageTracker()


@pytest.fixture
def llm(tracker: UsageTracker) -> FakeLLM:
    return FakeLLM(tracker)


@pytest.fixture
def classifier(tracker: UsageTracker) -> FakeClassifier:
    return FakeClassifier(tracker)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return tmp_path / "wiki"


@pytest.fixture
def classic_wiki(bundle: Path, llm: FakeLLM) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.init()
    return wiki


@pytest.fixture
def crow_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier)
    wiki.init()
    return wiki
