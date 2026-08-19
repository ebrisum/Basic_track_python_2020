# Recorded runs

`acceptance.json` is the output of the command `VALIDATION.md` documents:

    .venv/bin/python -m analysis.validate --games 10000 --check-every 1 --deep \
        --workers 4 --metrics runs/acceptance.json

It is committed rather than regenerated on demand for one reason: the numbers
in `VALIDATION.md` are prose, and prose drifts. This file carries the same
numbers with the provenance stamp attached, so a claim on that page can be
checked against the run that produced it — engine version, rules version, card
pool digest, and both schema digests included.

The `settings` block matters as much as the `metrics` block. Ten thousand
games checked at the end and ten thousand checked after every action are
different claims wearing the same digits, and `workers` explains a 4x swing in
the throughput line without changing any other number.

Regenerating it is a 26-minute job on four cores. Do that rather than editing
it: a measured artefact that has been hand-corrected is worse than no artefact.
