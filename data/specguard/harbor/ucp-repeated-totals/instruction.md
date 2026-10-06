Validate checkout totals according to the UCP signed-amount contract: there is exactly one total entry, and its amount equals the sum of all non-total entry amounts. Types other than subtotal and total may repeat, and type is an open string, so repeated tax or fee entries and custom types participate. Discount amounts carry their sign. Inputs are checkout objects with a totals list of type/amount records, an expected subtotal and expected discount; observables are acceptance or assertion failure of the totals consistency check.
Satisfy the existing checks associated with business_logic_test.py::BusinessLogicTest::assert_totals_consistent without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: the selected target is a reusable assertion helper receiving checkout_obj.totals and caller-supplied expected_subtotal/expected_discount parameters, rather than one concrete checkout fixture. Total entries expose a string type and numeric amount; discount comparison parameters may be scalar or a list, set, or tuple.
- Observations: the helper's intermediate selected/aggregated amounts and whether execution returns normally or raises an assertion, preserving the order of checks.
- Output shape: totals records, numeric intermediate values, and a completion-or-assertion-failure outcome. No concrete checkout or expected parameter values are supplied here, and these parameters must not be used to manufacture checkout data.
