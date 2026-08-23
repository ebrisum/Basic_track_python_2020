"""The neural policy/value model (`TCG_AI_BUILD.md` sections 17, 18, 22).

This package, and only this package, may import torch. Everything the model
consumes -- the observation, the token encoding, the action encoding, the
trajectory format -- is pure standard library and lives in `learning/`, which
`tests/test_stdlib_only.py` enforces. That boundary is the whole reason RQ-22's
dependency could be taken: a person who wants to play Riftbound, run the rules
tests, or reproduce the 10,000-game validation still installs nothing.
"""
