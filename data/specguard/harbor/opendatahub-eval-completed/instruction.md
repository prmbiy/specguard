EvalHub evaluation Jobs use the trustyai.opendatahub.io/evaluation-phase label to expose lifecycle transitions. A successful evaluation proceeds from Pending to Running to Completed. Preserve the distinct failure and threshold-violation states. Inputs include a known-good model/dataset evaluation request and the resulting batch Job metadata; the observable is the evaluation-phase label after all benchmarks complete successfully, distinct from any status annotation.
Satisfy the existing checks associated with tests/ai_safety/evalhub/k8s_lifecycle_signals/test_evalhub_lifecycle_job_label_phase.py::TestLblJobLabelPhase::test_lbl_002_job_label_set_to_succeeded_on_completion without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: use the configured EvalHub route, namespace, authentication/CA inputs, and model service; build the evaluation payload, submit it, resolve the associated Kubernetes Job, wait for the evaluation and lifecycle signals, then read its label.
- Observations: read_job_label on the resolved Job after those waits, using the evaluation-phase label key. Preserve the distinction between the label, status annotations, and the waiting operations.
- Output shape: Job metadata with string-keyed labels and an optional string-valued label lookup, plus lifecycle/operation state sufficient to identify the observation phase. Fixture-derived addresses and identifiers are not fixed values supplied by this description; helper behavior must come from its implementation, not an assertion's expected constant.
