"""
Evidence Normalizer.

Identifiers emit `Evidence` on a 0..1 "how strong is this single
observation" scale, independent of how much the system should
ultimately trust that identifier. The normalizer is the one place that
combines strength x identifier weight into an actual log-odds update,
so:

  - identifiers stay simple (they never think about logits/weights),
  - identifier weight can be tuned centrally without touching
    identifier code,
  - the log-odds scale is consistent across every identifier, which is
    what makes combining independent weak signals meaningful.

Log-odds (not raw probability averaging) is used because it's the
natural way to combine independent weak evidence: each new piece of
evidence just adds/subtracts from a running total, order doesn't
matter, and confidence saturates gracefully near 0/1 instead of
overshooting.
"""
from __future__ import annotations

from typing import Optional

from engine.core.schemas import Evidence, NormalizedEvidence

# One "full-strength, full-weight" piece of evidence shifts log-odds by
# this much. Tuned so that ~3-4 corroborating strong signals push
# confidence solidly past 0.9 without a single identifier ever being
# able to unilaterally decide the answer.
BASE_LOGIT_SCALE = 1.6

# `against_candidate` evidence dampens the *not_candidate* logit less
# than it boosts it when direction matches, and vice versa - i.e. "for
# candidate" evidence is weak counter-evidence for "not candidate" and
# vice versa. This asymmetry is why the two tracks are independent
# instead of complementary (see state_store.py docstring).
CROSS_TRACK_DAMPING = 0.35


def normalize(
    evidence: Evidence, identifier_weight: float, decay_half_life: Optional[float] = None
) -> NormalizedEvidence:
    weight = max(0.0, identifier_weight)
    magnitude = weight * evidence.strength * BASE_LOGIT_SCALE

    if evidence.direction == "for_candidate":
        delta_candidate = magnitude
        delta_not_candidate = -magnitude * CROSS_TRACK_DAMPING
    else:  # against_candidate
        delta_candidate = -magnitude
        delta_not_candidate = magnitude

    return NormalizedEvidence(
        evidence=evidence,
        identifier_weight=weight,
        delta_candidate_logit=delta_candidate,
        delta_not_candidate_logit=delta_not_candidate,
        # Decay itself has nothing to do with normalization - it's not
        # a function of this one observation's magnitude, it's a
        # function of elapsed time, applied later at Belief Engine read
        # time. This is just the pass-through point that gets the
        # identifier's configured half-life from "known at evidence
        # time" to "known when the belief engine buckets it."
        decay_half_life=decay_half_life,
    )
