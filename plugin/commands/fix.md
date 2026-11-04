---
description: Propose the most urgent fix and apply it only after you approve
argument-hint: "[incident id]"
---
Use the deliverability-analyst skill. Take incident "$ARGUMENTS", or the most urgent open
incident from `sitrep`. Read it with `incident`, then call `preview_change` for its first
step and show the user exactly what would change and how undo works. Ask for approval.
Only after the user says yes, call `apply_change`. If they want it done differently
(warmup off too, a daily limit instead of a pause), preview that with `propose_change`
first. If the step can't be automated, say precisely what to do by hand.
