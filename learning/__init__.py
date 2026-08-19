"""The adapter layer between the frozen engine interface and a learning stack.

`TCG_AI_BUILD.md` sections 7, 14 and 16 all describe things that sit *between*
the rules engine and a model: an action encoding, a trajectory format, a state
encoder. None of them are rules, and putting them in `engine/` would blur the
layer separation the project has kept from the first commit.

Everything here reads a `RiftboundObservation` and a list of `Action`s -- the
same two things an agent gets -- and nothing here may reach into
`RiftboundState`. That is not a style preference: an encoder with state access
would hand a model information the interface does not grant, and no leakage
test would catch it, because the leak would be in the encoder rather than the
observation.
"""
