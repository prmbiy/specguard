Follow ADR-0041 when initializing vulnerability-case participants: report validation sends a proposal to the CaseActor, which creates the canonical VulnerabilityCase with its initial participants. Continue participant initialization on that canonical case, not a separate vendor-local case without the report link. Inputs include finder, vendor and coordinator actors, a vulnerability report, validation activities and stored case objects. Observables are the selected case identity and participant membership before and after coordinator initialization.
Satisfy the existing checks associated with test/demo/test_initialize_participant_demo.py::test_demo without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: the demo fixture configures a test application/data layer and routes demo requests through its test client. Capture logs at INFO level while invoking demo.main(skip_health_check=True), then inspect the captured text after the call.
- Observations: the complete captured log string, including messages arising from participant initialization and error reporting, rather than only final case/participant records.
- Output shape: a workflow result/state trace and emitted log text, with selected-case identity and participant identities available at the appropriate stages. Preserve the demo sequence and its logging behavior; describing log observations does not prescribe which messages must be present.
