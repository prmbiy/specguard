Ensure that DataFrame.eval with inplace=False returns a new DataFrame containing the evaluated assignment and does not modify the original DataFrame. For example, evaluating "c = a + b" should add column c to the returned DataFrame only. Inputs are a DataFrame with numeric columns a and b and an assignment expression with inplace=False; observables are the eval return value and the original DataFrame's contents after the call. Preserve this documented behavior while satisfying the assertions in TestOperations.test_assignment_not_inplace in pandas/tests/computation/test_eval.py.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: construct a five-row, two-column DataFrame from default_rng(2).standard_normal((5, 2)), with columns a and b, then call df.eval("c = a + b", inplace=False). Afterward the check also copies df and performs an assignment on that copy.
- Observations: whether the eval return value is None and the original df's frame contents after the call. Retain the separately constructed comparison frame as distinct from both df and the eval return value.
- Output shape: an optional returned DataFrame and the original DataFrame's post-call state, including row/column labels and numeric cell values. Distinguish these objects and the later copy/assignment operation; do not collapse them into a single frame.
