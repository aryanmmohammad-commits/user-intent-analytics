---
name: monthly-review
description: Monthly review of the PQA score against logged call outcomes (Revenue Signals, step 6). Reports what happened after the Monday lists and proposes at most one weight change, only when the evidence gate is open. Use when the user asks for the monthly review.
disable-model-invocation: true
argument-hint: "[since YYYY-MM-DD] [until YYYY-MM-DD]"
---

Run the monthly review of the Revenue Signals score. You are the analyst; a human decides.

1. Call `review_outcomes`. If the user gave dates ($ARGUMENTS), pass them as `since` and `until`;
   otherwise use the default window (the 30 days up to the latest run date).
2. Report the counts first, naming the metrics and the window: PQAs on the lists, accounts called,
   reached, meetings booked, and the meeting rate of called accounts. Say the score is v0 and
   unvalidated.
3. If `enough_evidence` is false: say plainly that there is not enough evidence to change any weight,
   give the number of called accounts against the minimum, and stop. Do not call
   `propose_weight_change`, and do not suggest new weights.
4. If `enough_evidence` is true:
   - Compare the meeting rate of PQAs with other called accounts, then each signal with and without
     it as a reason. Treat a gap under 10 percentage points, or any group under 10 accounts, as noise.
   - Only if one signal shows a clear gap that has a plausible business reason, propose one change
     with `propose_weight_change`: a modest step (at most half the current weight, up or down) and a
     rationale that cites the numbers from the review.
   - Say that reps choose whom to call, so this is evidence, not proof.
5. End with three lines: what was checked, the decision (no change, or the pull request link), and
   what a human must do next (review and merge the pull request, then run scripts/weekly_refresh.ps1).

Never invent or estimate outcomes. Never change a weight any other way than `propose_weight_change`.
