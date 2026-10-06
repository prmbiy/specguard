FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config curl ca-certificates git bubblewrap docker.io libpq-dev libmagic1 && rm -rf /var/lib/apt/lists/*
WORKDIR /testbed
RUN curl -fsSL https://codeload.github.com/vllm-project/vllm/tar.gz/2131b597b18d051dced4c4a605d362fa37f46ed1 | tar -xz --strip-components=1
RUN pip install --no-cache-dir pytest packaging typing_extensions
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir numpy regex psutil
# Only the pure-Python environment flag test is in scope; no GPU engine is installed.
# Tool code and credentials are supplied by the trusted launcher, not baked in.
ENV PYTHONPATH=/opt/specguard/src
RUN pip install --no-cache-dir pyyaml tqdm anthropic openai httpx
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-28.0.4.tgz | tar -xz -C /usr/local/bin --strip-components=1 docker/docker
COPY specguard /usr/local/bin/specguard
RUN chmod 755 /usr/local/bin/specguard && git config --system --add safe.directory '*'
CMD ["sleep", "infinity"]
