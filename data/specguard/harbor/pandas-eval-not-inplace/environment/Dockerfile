FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config curl ca-certificates bubblewrap docker.io \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'numpy>=2' 'Cython>3.1,<4' 'meson-python>=0.17.1,<1' \
    'meson<2' ninja wheel 'versioneer[toml]' python-dateutil pytest hypothesis \
    pytest-xdist numexpr pytz tzdata pyyaml tqdm anthropic openai httpx
WORKDIR /testbed
RUN curl -fsSL https://codeload.github.com/pandas-dev/pandas/tar.gz/04a5f9741d754513bbfc26bc2f048ca36bed6725 \
    | tar -xz --strip-components=1
RUN pip install --no-build-isolation --no-deps . -Ccompile-args=-j4
# Install the matching compiled extensions into the original source tree so tests
# run against this exact revision, not a different release's wheel.
RUN python -c "import pathlib, shutil; src=pathlib.Path('/usr/local/lib/python3.12/site-packages/pandas'); dst=pathlib.Path('/testbed/pandas'); [(shutil.copy2(p, dst/p.relative_to(src))) for p in src.rglob('*.so')]"
RUN cp /usr/local/lib/python3.12/site-packages/pandas/_version_meson.py /testbed/pandas/_version_meson.py
# CLI snapshot is supplied separately by the trusted demo launcher. No credentials,
# research annotations, fixed tests or Git history are baked into the task image.
ENV PYTHONPATH=/opt/specguard/src
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-28.0.4.tgz \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker
COPY specguard /usr/local/bin/specguard
RUN chmod 755 /usr/local/bin/specguard
# Lake inspects the pinned Mathlib checkout's remote; Git is a trusted-runtime
# dependency only (it is not included in the agent's executable allowlist).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
CMD ["sleep", "infinity"]
