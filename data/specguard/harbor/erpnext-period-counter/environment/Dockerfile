FROM python:3.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config curl ca-certificates git bubblewrap docker.io libpq-dev libmagic1 && rm -rf /var/lib/apt/lists/*
WORKDIR /testbed
RUN curl --retry 3 -fsSL https://codeload.github.com/frappe/erpnext/tar.gz/1dd0b7dc9e7c687c9c051562d45f6fa91d51eaea -o /tmp/repository.tar.gz && tar -xzf /tmp/repository.tar.gz --strip-components=1 && rm /tmp/repository.tar.gz
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-mock
RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/opt/specguard/src
RUN pip install --no-cache-dir pyyaml tqdm anthropic openai httpx
RUN curl --retry 3 -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-28.0.4.tgz -o /tmp/docker.tgz && tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker && rm /tmp/docker.tgz
COPY specguard /usr/local/bin/specguard
RUN chmod 755 /usr/local/bin/specguard && test ! -e /testbed/.git && test -f erpnext/controllers/tests/test_accounts_controller.py
CMD ["sleep", "infinity"]
