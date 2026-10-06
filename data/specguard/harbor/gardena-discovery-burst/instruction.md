Collapse a burst of unknown-device discovery requests into one pending scheduled run. If a discovery handle is already pending, leave it intact instead of cancelling or rescheduling it. Inputs include two consecutive device IDs dev-1 and dev-2, an event loop call_later operation and the returned handle. Observables are the number of scheduling calls, cancellation calls, and the retained pending handle; this scheduling policy does not require waiting for wall-clock time.
Satisfy the existing checks associated with tests/test_coordinator.py::test_schedule_unknown_device_discovery_reschedules_on_burst without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: construct an unconnected coordinator and replace its event-loop call_later with a mock whose successive calls return two distinct objects, first_handle and second_handle. Submit dev-1 and then dev-2 without executing a scheduled callback in between.
- Observations: after both requests, inspect cancel-call counts separately on each mock handle and the identity of coordinator._unknown_device_discovery_handle.
- Output shape: a scheduling-call trace with an ordered supply of returned handles, per-handle integer cancellation counts, and an optional retained-handle identity. Both mock objects exist even if one is never returned by a scheduling call; do not merge their identities or cancellation counters.
