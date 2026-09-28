# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The llm-wiki-v2 HTTP API and web page (llmwiki2 serve). Run it with docker-compose.yml.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="llm-wiki-v2" \
      org.opencontainers.image.description="LLM wiki in a Markdown format derived from the Open Knowledge Format" \
      org.opencontainers.image.source="https://github.com/fed3c3sa/llm-wiki-v2" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

# Settings come from the environment (.env): OKF_CONFIG names a settings file the image never has.
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore \
    OKF_BUNDLE=/data/wiki OKF_CONFIG=/app/no-settings-file.json

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY LICENSES ./LICENSES
COPY src ./src
RUN pip install ".[server]" && useradd --uid 1000 --no-create-home wiki

# Docker creates a missing ./wiki as root: hand it to "wiki", then drop root and
# run as the folder's owner, so on Linux the files on the host stay yours.
COPY --chmod=755 <<'EOF' /usr/local/bin/okf-entrypoint
#!/bin/sh
set -e
if [ "$(id -u)" = 0 ]; then
  mkdir -p "$OKF_BUNDLE"
  [ "$(stat -c %u "$OKF_BUNDLE")" != 0 ] || chown wiki:wiki "$OKF_BUNDLE"
  exec setpriv --reuid="$(stat -c %u "$OKF_BUNDLE")" --regid="$(stat -c %g "$OKF_BUNDLE")" --clear-groups "$@"
fi
exec "$@"
EOF

EXPOSE 8000
ENTRYPOINT ["okf-entrypoint"]
CMD ["llmwiki2", "serve", "--host", "0.0.0.0", "--port", "8000"]
