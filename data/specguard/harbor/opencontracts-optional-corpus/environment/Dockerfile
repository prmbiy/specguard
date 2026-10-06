FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config curl ca-certificates git bubblewrap docker.io libpq-dev libmagic1 && rm -rf /var/lib/apt/lists/*
WORKDIR /testbed
RUN curl -fsSL https://codeload.github.com/Open-Source-Legal/OpenContracts/tar.gz/68a1264498a724065c96aa0feee163fec585eff5 | tar -xz --strip-components=1
RUN pip install --no-cache-dir -r requirements/local.txt
# Tool code and credentials are supplied by the trusted launcher, not baked in.
ENV PYTHONPATH=/opt/specguard/src
RUN pip install --no-cache-dir pyyaml tqdm anthropic openai httpx
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-28.0.4.tgz | tar -xz -C /usr/local/bin --strip-components=1 docker/docker
COPY specguard /usr/local/bin/specguard
RUN chmod 755 /usr/local/bin/specguard && git config --system --add safe.directory '*'
CMD ["sleep", "infinity"]
