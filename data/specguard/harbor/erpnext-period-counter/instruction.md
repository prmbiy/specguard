Document Naming Rule counters are per resolved prefix. When use_posting_datetime_for_naming_documents is enabled, resolve MM and YYYY from posting_date rather than today; a new month/year prefix starts its own counter at 00001 and later documents sharing that prefix increment it. Inputs include Sales Invoices dated 2025-12-31 and 2026-01-01, set_posting_time=1, and a naming rule with SI-, MM, YYYY and a five-digit counter. The observable is each saved invoice name.
Satisfy the existing checks associated with erpnext/controllers/tests/test_accounts_controller.py::TestAccountsController::test_document_naming_rule_based_on_posting_date without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: enable posting-datetime-based naming, insert a Sales Invoice naming rule with prefix SI-.MM.-.YYYY.-, then create and save two separate invoices in sequence, with set_posting_time=1 and posting dates 2025-12-31 and 2026-01-01.
- Observations: read each invoice's name immediately after its save.
- Output shape: an ordered sequence of name strings with literal prefix, month/year components, and a formatted numeric counter, together with the naming-counter state between saves. Input posting dates are distinct from the current date.
