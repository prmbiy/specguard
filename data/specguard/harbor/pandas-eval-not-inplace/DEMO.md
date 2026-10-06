# pandas: non-mutating eval versus a faulty assertion

**Task:** `DataFrame.eval("c = a + b", inplace=False)` should return the added
column in a new DataFrame while leaving the original unchanged.

**Original test (key lines, unchanged):**

```python
actual = df.eval("c = a + b", inplace=False)
assert actual is not None
expected = df.copy()
expected["c"] = expected["a"] + expected["b"]
tm.assert_frame_equal(df, expected)
```

**Observed execution:** the original `df` has shape `(5, 2)`; `expected` has shape
`(5, 3)`. The final assertion fails with `DataFrame shape mismatch`.

The test compares **df**, not **actual**. Demanding equality here would require
the original to gain a column, contrary to the non-mutating task.

**SpecGuard status:** end-to-end verification is still in progress. No successful
Lean certificate has been obtained yet; do not present this reproduction as one.

This is a development demonstration using an older genuine bug, not a fresh-2026
evaluation result. The instruction is researcher-written. Reproduction runs the
unchanged fixture-free method with `--runxfail --noconftest`; the full pandas suite
has not been validated in this image.
