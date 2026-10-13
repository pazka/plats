# syntax=docker/dockerfile:1
# Sandbox of the catalogue reviewers (scripts/review_dishes.py, default --sandbox docker):
#   target "reviewer": one throwaway container per `claude -p` (no tools, no volumes, read-only,
#                      non-root, no capabilities, on an internal network with no route out);
#   target "egress":   the allowlist proxy that is that network's only exit (api.anthropic.com:443).
# Built locally by the script (docker build --target …); never pushed, never part of the plats image.

ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ${NODE_IMAGE} AS reviewer
# Pinned: a reviewed CLI version, not "latest". Bump deliberately.
ARG CLAUDE_CODE_VERSION=2.1.287
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false
RUN npm install -g --omit=dev "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /root/.npm \
           /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg \
 && groupadd -g 10002 reviewer && useradd -u 10002 -g 10002 -d /home/reviewer -M -s /usr/sbin/nologin reviewer
ENV HOME=/home/reviewer \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1 \
    DISABLE_BUG_COMMAND=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
USER 10002:10002
WORKDIR /home/reviewer
ENTRYPOINT ["claude"]

FROM ${PYTHON_IMAGE} AS egress
COPY egress_proxy.py /egress_proxy.py
USER 65534:65534
EXPOSE 3128
ENTRYPOINT ["python3", "-I", "-u", "/egress_proxy.py"]
