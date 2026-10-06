FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git xz-utils unzip g++ make && rm -rf /var/lib/apt/lists/*
ENV ELAN_HOME=/opt/specguard-lean/elan
ENV PATH=/opt/specguard-lean/elan/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /opt/specguard-lean
# Pin the installer as well as Lean, Mathlib and REPL.
RUN curl -fL https://github.com/leanprover/elan/releases/download/v4.2.3/elan-$(uname -m)-unknown-linux-gnu.tar.gz \
    -o /tmp/elan.tar.gz && tar -xzf /tmp/elan.tar.gz -C /tmp && \
    /tmp/elan-init -y --no-modify-path --default-toolchain leanprover/lean4:v4.24.0 && \
    rm /tmp/elan.tar.gz /tmp/elan-init
COPY lean-toolchain lakefile.toml lake-manifest.json /opt/specguard-lean/workspace/
WORKDIR /opt/specguard-lean/workspace
RUN mkdir -p .lake/packages/mathlib && \
    git -C .lake/packages/mathlib init && \
    git -C .lake/packages/mathlib remote add origin https://github.com/leanprover-community/mathlib4 && \
    git -C .lake/packages/mathlib fetch --depth 1 origin f897ebcf72cd16f89ab4577d0c826cd14afaafc7 && \
    git -C .lake/packages/mathlib checkout --detach FETCH_HEAD && \
    lake exe cache get && \
    printf 'export LEAN_PATH="%s"\nexport LEAN_SYSROOT="%s"\n' \
    "$(lake env printenv LEAN_PATH)" "$(lake env printenv LEAN_SYSROOT)" > /opt/specguard-lean/runtime-env.sh
WORKDIR /opt/specguard-lean/repl
RUN git init && git remote add origin https://github.com/leanprover-community/repl.git && \
    git fetch --depth 1 origin 8fff8552292860d349b459d6a811e6915671dc0d && \
    git checkout --detach FETCH_HEAD && \
    test "$(cat lean-toolchain)" = "leanprover/lean4:v4.24.0" && lake build repl
COPY run-repl.sh /opt/specguard-lean/run-repl.sh
RUN chmod 755 /opt/specguard-lean/run-repl.sh
WORKDIR /opt/specguard-lean/workspace
ENTRYPOINT ["/opt/specguard-lean/run-repl.sh"]
