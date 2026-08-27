# services/skill_tiers.py
"""Single source of truth for turning a mastery percentage into a tier label.

Badge creation (badge_service.generate_badges_for_user), badge-level lookups
(badge_service.get_current_badge_level), and the student progress summary
(canvas_submissions_service's Progress-collection rebuild) all need this same
mapping. Previously each computed it independently with different cutoffs,
which made the same percentage show different tier language across screens.
"""

TIER_THRESHOLDS = (
    (90, 'expert'),
    (75, 'advanced'),
    (50, 'intermediate'),
    (25, 'beginner'),
)


def tier_for_score(percentage: float) -> str:
    """Map a 0-100 mastery percentage to a tier label.

    Returns one of: 'expert', 'advanced', 'intermediate', 'beginner', 'none'.
    """
    for threshold, label in TIER_THRESHOLDS:
        if percentage >= threshold:
            return label
    return 'none'
