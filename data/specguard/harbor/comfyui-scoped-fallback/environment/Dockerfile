FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config curl ca-certificates git bubblewrap docker.io libpq-dev libmagic1 && rm -rf /var/lib/apt/lists/*
WORKDIR /testbed
RUN curl -fsSL https://codeload.github.com/Comfy-Org/ComfyUI-Manager/tar.gz/2b40deba7d04afeee29ee70c88f6c336e43dc9ca | tar -xz --strip-components=1
RUN pip install --no-cache-dir pytest
# Selected tests inspect repository source and need no ComfyUI server.
# Tool code and credentials are supplied by the trusted launcher, not baked in.
ENV PYTHONPATH=/opt/specguard/src
RUN pip install --no-cache-dir pyyaml tqdm anthropic openai httpx
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-28.0.4.tgz | tar -xz -C /usr/local/bin --strip-components=1 docker/docker
COPY specguard /usr/local/bin/specguard
RUN chmod 755 /usr/local/bin/specguard && git config --system --add safe.directory '*'
CMD ["sleep", "infinity"]
