Subscription-table column selection must reflect that Select all rows is a control, not a named data column. When optional columns are unchecked the remaining named column is Name; selecting all optional columns returns those named columns plus Name, without adding a Select all rows data column. Inputs are a subscription table and a mapping of optional column names to checkbox states; observables are the headers returned by filter_columns.
Satisfy the existing checks associated with tests/foreman/ui/test_subscription.py::test_select_customizable_columns_uncheck_and_checks_all_checkboxes without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: select an organization and import its subscription manifest. Call filter_columns with all optional checkbox states false, then with them true. The optional names are SKU, Contract, Start date, End date, Requires virt-who, Type, Entitlements, and Hosts.
- Observations: the header collection returned by each call; the first is compared as a sequence and the second is converted to a set.
- Output shape: phase-specific collections of header strings, preserving order where observed and set membership where converted. Keep selection controls distinguishable from named data columns without supplying an expected header collection.
