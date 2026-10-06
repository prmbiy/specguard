Preserve the generic HTTP-403 fallback copy within handle403Response and keep its existing fallback occurrences in that function. The install_pip and install_via_git_url surfaces may supply flag-specific default messages; other handle403Response callers must remain unchanged. Legitimate reuse of the same generic sentence outside handle403Response is permitted. Inputs to this structural check are the JavaScript source text and function/call boundaries; observables are fallback occurrences inside that function and defaultMessage arguments at its callers. Satisfy tests/test_install_flags_structural.py::JsCopyStructuralTest::test_generic_fallback_and_frozen_callers_unchanged under this scoped contract.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: read js/common.js as complete source text; extract the install_pip and install_via_git_url function blocks and their handle403Response argument strings; then scan the source of each js/*.js file.
- Observations: the whole common.js file's exact-substring count and the collection of extracted two-argument call strings across the scanned files. These are source-text observations, not executed HTTP responses.
- Output shape: file-path-to-source-text mappings, function/call boundaries, integer occurrence counts, and collections of argument strings. Retain text outside handle403Response as well as its body. This observation scope does not broaden the function-scoped behavior requirement above.
