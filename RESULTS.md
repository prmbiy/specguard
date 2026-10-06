# Experiment results

Results behind the figures and numbers reported in the paper, as fractions of the full task count, including inconclusive outcomes. The main comparison figure is [docs/figures/results.pdf](docs/figures/results.pdf). Full settings, scoring, bootstrap confidence intervals, and the failure analysis are in the paper's appendices.

## Definitions

- **Oneoff:** the agents see only the mutated (corrupted) test suite. This is the deployment-realistic setting and the main result.
- **Paired:** the TestAgent sees the original and mutated test versions together. Used to isolate the test-spec alignment bottleneck (paper §5.2.3).
- **Original:** the agents see the clean, unmutated suite. Measures false positives.
- **Detected:** a correct conflict verdict, with the complete predicted test-result vector matching ground truth. **Certified:** the subset of detections backed by a kernel-checked Lean proof. **Execution-only:** the remaining detections, supported by executing the generated specification. Detected = certified + execution-only.
- **FN:** a conflicted task called non-conflicting. **FP:** a clean task called conflicting. **Inconclusive:** no usable decision; this is a deliberate third verdict, not an error category.
- For conflicted-task tables, detected + FN + other wrong + inconclusive = 1.000.

## Main results: SWE-bench oneoff (349 tasks)

One run per model; GPT-5.6 Sol is the mean of three repetitions (below).

| Model | Detected | Certified | Execution-only | FN | Inconclusive |
| --- | --- | --- | --- | --- | --- |
| GPT-5.6 Sol | 0.570 | 0.511 | 0.059 | 0.081 | 0.349 |
| Claude Opus 5 | 0.705 | 0.470 | 0.235 | 0.043 | 0.252 |
| Claude Fable 5 | 0.728 | 0.496 | 0.232 | 0.066 | 0.206 |
| Kimi K3 | 0.585 | 0.479 | 0.106 | 0.086 | 0.330 |

### GPT-5.6 Sol repetitions

| Run | Detected | Certified | FN | Inconclusive |
| --- | --- | --- | --- | --- |
| 1 | 0.559 | 0.504 | 0.066 | 0.375 |
| 2 | 0.556 | 0.496 | 0.089 | 0.355 |
| 3 | 0.596 | 0.533 | 0.089 | 0.315 |
| Mean | 0.570 | 0.511 | 0.081 | 0.349 |
| SD | 0.022 | 0.020 | 0.013 | 0.031 |

## False positives: SWE-bench original controls (349 tasks)

One original run per model. These come from separate runs on the clean suite and are not part of the oneoff rows.

| Model | Cleared | FP | Inconclusive |
| --- | --- | --- | --- |
| GPT-5.6 Sol | 0.756 | 0.060 | 0.183 |
| Claude Opus 5 | 0.808 | 0.034 | 0.158 |
| Claude Fable 5 | 0.805 | 0.040 | 0.155 |
| Kimi K3 | 0.602 | 0.120 | 0.278 |

## Method comparison (GPT-5.6 Sol, 349 tasks)

| Method | Setting | Detected | Certified | FN | FP | Inconclusive |
| --- | --- | --- | --- | --- | --- | --- |
| SpecGuard | oneoff | 0.570 | 0.511 | 0.081 | 0.060 | 0.349 |
| SpecGuard | paired | 0.739 | 0.670 | 0.049 | — | 0.212 |
| Judge Agent (k=5 majority vote) | oneoff + original | 0.602 | n/a | 0.398 | 0.023 | 0.000 |
| Python Reference Model | paired | 0.748 | n/a | 0.032 | — | 0.221 |

Judge Agent detections are model verdicts with no proof tier; it was allowed to return inconclusive and its majority verdicts never did. The Python Reference Model requires paired tests by construction (its model is checked by executing the disputed tests against it), and its detections are execution evidence with no Lean certificates. Paired rows are an easier information setting than oneoff and are not directly comparable to the oneoff rows.

## Effect of paired tests (349 tasks)

| Model | Detected | Certified | FN | Inconclusive |
| --- | --- | --- | --- | --- |
| GPT-5.6 Sol | 0.739 | 0.670 | 0.049 | 0.212 |
| Claude Opus 5 | 0.871 | 0.582 | 0.037 | 0.092 |
| Claude Fable 5 | 0.894 | 0.602 | 0.046 | 0.060 |
| Kimi K3 | 0.837 | 0.670 | 0.037 | 0.115 |

Relative to oneoff, detection rises by 0.17 to 0.25 and certification by 0.11 to 0.19 across models (paper §5.2.3).

## Beyond SWE-bench

### Real-world conflicts (SpecGuard CLI, 22 tasks)

Human-vetted, naturally occurring test conflicts from recent GitHub issues and pull requests, run with GPT-5.6 Sol through the standalone CLI.

| Conflict | Certified | Execution-only | No-conflict | Inconclusive |
| --- | --- | --- | --- | --- |
| 0.409 | 0.364 | 0.045 | 0.045 | 0.545 |

### LiveCodeBench (95 mutated tasks, GPT-5.6 Sol)

| Detected | FN | Other wrong | Inconclusive |
| --- | --- | --- | --- |
| 0.842 | 0.000 | 0.042 | 0.116 |

All detections are execution-checked; no Lean certificates are recorded in this track.

### Specification fidelity (VERINA, 187 tasks)

A fraction of 0.963 of generated specifications are proven bidirectionally equivalent to the human-written reference specifications in Lean, using the grind tactic.

## Cost

Mean API cost per task, OpenRouter with input caching.

| System | Model | Cost per task |
| --- | --- | --- |
| SpecGuard (oneoff) | GPT-5.6 Sol | $0.26 |
| SpecGuard (paired) | GPT-5.6 Sol | $0.46 |
| SpecGuard (oneoff) | Claude Opus 5 | $0.43 |
| SpecGuard (oneoff) | Claude Fable 5 | $0.70 |
| SpecGuard (oneoff) | Kimi K3 | $1.14 |
| Judge Agent (k=5) | GPT-5.6 Sol | $0.84 |
| Python Reference Model (paired) | GPT-5.6 Sol | $0.45 |

## Notes

- Evidence tiers count successful predictions only; a proof attached to an incorrect prediction is not counted as a certified detection.
- The GPT-5.6 Sol false-positive rate comes from one original-control run, not three.
- Raw agent logs and API traffic are not part of this repository.