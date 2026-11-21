# Synthetic example (not an official case result)

This directory is a **synthetic** input/output example for the benchmark runner.
All sentences, numbers and predictions are invented for interface testing; they
are not organizer materials and not formal answers.

Run it:

```bash
python3 scripts/run_benchmark_case.py run \
  --input examples/synthetic-case/input \
  --work examples/synthetic-case/work \
  --output /tmp/bench-example-output
```

Expected: `status=answered`, 4 per-item rows, `answer_accuracy=1.0`,
`deterministic=true`, all original `output_contract` keys covered.

Boundary check: delete one prediction row and rerun `finalize`; `coverage`
drops to 0.75, the missing item gets `failure_type=missing_prediction`, and the
status becomes `partial` instead of pretending the run was complete.
