# SpecGuard Harbor tasks

22 packaged cases. Each directory contains the instruction, pinned Docker recipe,
unchanged upstream test invocation, task settings, and host-only research provenance.
`provenance.json` records the actual image build status and log location.

**Building an image does not establish test reproduction or a SpecGuard verdict.**
Some images provide source inspection and Python tooling, not the external systems
or native builds needed to execute the original test. Those requirements are explicit
in each task's README and provenance. No tests, expected values, skip/xfail markers,
or implementation code were rewritten to make a case runnable.

| Task directory | Execution requirements / scope |
|---|---|
| pandas-eval-not-inplace | Existing pandas development case |
| comfyui-scoped-fallback | Python structural checks over JavaScript source |
| vllm-precompiled-flags | Existing environment-flag case; CPU tooling |
| haystack-stream-usage | Mocked OpenAI chunks; SDK-dependent serialization |
| openhands-search-limit | Existing schema-limit case |
| opencontracts-optional-corpus | Django/tool-context setup; see task README |
| ray-actor-order | Matching native Ray build; distributed execution |
| polars-topk-order | Matching Rust/Polars extension build |
| anyllm-multiturn-tools | Live-provider integration test; no keys/network supplied |
| erpnext-period-counter | Compatible Frappe, bench site, MariaDB, Redis |
| defectdojo-note-privacy | Full DefectDojo requirements/settings/database/fixtures |
| borg-preopen-atime | Windows/NTFS and native Borg; Linux image is inspection-only |
| xtgeo-rms-precision | Native XTGeo and proprietary Roxar/RMS with project fixtures |
| gardena-discovery-burst | Home Assistant test dependencies; mocked scheduler |
| unsloth-mcp-tool-names | CPU MCP inspection utilities; not a full inference deployment |
| sonic-frr-loopback-acl | SONiC hardware/testbed, topology and matching host-services image |
| ucp-repeated-totals | Merchant/reference server for upstream integration callers |
| vultron-canonical-case | Application dependencies; original demo xfail retained |
| ckan-include-users-default | Full CKAN requirements/config, PostgreSQL, Solr, Redis |
| kornia-bfloat16-mix | CPU PyTorch; original known-failure policy retained |
| robottelo-subscription-columns | Foreman/Katello, browser and matching Airgun |
| opendatahub-eval-completed | OpenShift/Kubernetes, EvalHub and model/dataset fixtures |

Use the shared configuration and explicitly select a task:

```bash
uv run specguard-bench --config configs/specguard.yaml --task TASK_ID
```

Alternatively edit `tasks:` in `configs/specguard.yaml`. Packaging does not enable
new tasks automatically or launch paid calls. The six previously prepared task
packages are unchanged by the remaining-case packaging step.
