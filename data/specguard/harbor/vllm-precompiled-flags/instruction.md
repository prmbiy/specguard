Preserve the environment-variable semantics for precompiled installation. A nonempty VLLM_PRECOMPILED_WHEEL_LOCATION enables VLLM_USE_PRECOMPILED. VLLM_USE_PRECOMPILED_RUST controls the Rust frontend independently and must not disable the C-extension setting. Inputs are string-valued environment variables, including a wheel path and a Rust flag of "1"; outputs are the two Boolean settings. Satisfy tests/test_envs.py::test_precompiled_install_flags_are_orthogonal without changing these semantics.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: patch the environment with VLLM_PRECOMPILED_WHEEL_LOCATION="/tmp/vllm.whl" and VLLM_USE_PRECOMPILED_RUST="1", using clear=False, then evaluate the two environment-variable getter functions.
- Observations: each getter's Boolean return value while the environment patch is active.
- Output shape: two independently named Boolean settings derived from string-valued environment inputs. Preserve the distinction between absent, empty, and nonempty environment values; do not replace both settings with one combined flag.
