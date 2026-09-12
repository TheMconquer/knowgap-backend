# services/skill_tiers.py


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


def is_mastered(percentage: float) -> bool:

    return tier_for_score(percentage) not in ('none', 'beginner')
