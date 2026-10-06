Preserve top_k_by selection semantics without guaranteeing the ordering of its output. Separate top_k_by expressions may order their results independently; row-wise alignment between independent results is not promised. Inputs are DataFrames with numeric a and b columns and a string c column, selecting the top two values using c/a or c/b keys. Observables are the selected values in each output column, including their ordering.
Satisfy the existing checks associated with py-polars/tests/unit/operations/test_top_k.py::test_top_k without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: the selected test covers Series, DataFrame, and expression forms of top_k/bottom_k and top_k_by/bottom_k_by, including grouping, reverse options, nulls, and explicit sorting. Its six-row example uses a=[1,2,3,4,5,6], b=[12,11,10,9,8,7], and c=["Apple","Orange","Apple","Apple","Banana","Banana"].
- Observations: returned Series/DataFrames, each named column's values and ordering, row associations across independently selected columns, and exceptions for invalid argument shapes. Preserve which operations explicitly sort their result and which comparison helpers ignore row order.
- Output shape: typed, named columns with nullable scalar values and ordered rows, including grouped/exploded results; represent exceptions separately. Observability of row order does not add an ordering guarantee to the intended contract.
