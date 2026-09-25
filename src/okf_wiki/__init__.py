# Copyright 2026 Federico Cesarini, Marco Sassarini
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

"""An LLM wiki that writes Open Knowledge Format bundles.

A Librarian files sources into a folder tree of notes, a Researcher answers
questions from it; in CROW mode a typed classifier takes the decisions and the
LLM only writes text.
"""

from __future__ import annotations

__version__ = "0.2.0"

from okf_wiki.config import WikiConfig  # noqa: E402
from okf_wiki.wiki import Wiki  # noqa: E402

__all__ = ["Wiki", "WikiConfig", "__version__"]
