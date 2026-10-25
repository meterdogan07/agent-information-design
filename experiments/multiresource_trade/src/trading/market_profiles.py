"""Frozen numerical inputs for the active and ablation market panels.

This file contains data, not game logic.  A profile tuple specifies inventories
and per-unit values; ``markets.py`` decides which profiles belong to a panel.
"""

VALUE_TYPES = {
    "moderate": (3, 9),
    "strong": (2, 10),
    "very_strong": (1, 11),
}
PROPOSER_EXTRACTION_TYPE_PAIRS = (
    ("moderate", "strong"),
    ("moderate", "very_strong"),
    ("strong", "very_strong"),
)
SYMMETRIC_EXTRACTION_PROFILES = (
    ("matched_gap_1_11", (1, 11)),
    ("matched_gap_2_10", (2, 10)),
    ("matched_gap_3_9", (3, 9)),
)
RESPONDER_EXTRACTION_PROFILES = (
    ("high_gap_1_10", "near_flat_4_5", (1, 10), (4, 5)),
    ("high_gap_1_11", "near_flat_4_6", (1, 11), (4, 6)),
    ("high_gap_2_11", "near_flat_5_6", (2, 11), (5, 6)),
)

# Constant-severity resource scaling. Each added resource pair repeats the same
# extraction geometry at a distinct positive scale, which prevents rank ties.
CONSTANT_EXTRACTION_TARGET = 0.50
CONSTANT_EXTRACTION_TOLERANCE = 0.065
CONSTANT_EXTRACTION_PAIR_SCALES = (10, 11, 13)
CONSTANT_EXTRACTION_BASE_PROFILES = (
    ("constant_1", 5, (1, 10), (5, 4)),
    ("constant_2", 4, (1, 11), (6, 4)),
    ("constant_3", 5, (2, 11), (6, 5)),
)
CONSTANT_EXTRACTION_PROFILES = tuple(
    (
        profile_name,
        tuple(
            (
                quantity,
                a_values[0] * scale,
                a_values[1] * scale,
                b_values[0] * scale,
                b_values[1] * scale,
            )
            for scale in CONSTANT_EXTRACTION_PAIR_SCALES
        ),
    )
    for profile_name, quantity, a_values, b_values
    in CONSTANT_EXTRACTION_BASE_PROFILES
)

# Resource-complexity study. Every profile is a nested sequence
# of five complementary resource pairs.  A tuple is:
#
#   (abundant quantity, A-owned value, A-received value,
#    B-received value, B-owned value)
#
# Pair 1 is the original responder-extraction core.  Pairs 2 and 3 create
# offsetting compensation needs while preserving full exchange as first-best.
# Pairs 4 and 5 are selective: only one direction of each pair is efficient,
# so blindly exchanging every abundant resource is no longer optimal at ten
# resources.  The profiles were frozen using only researcher-side payoff
# constraints; no LLM outcomes were used to select them.
HETEROGENEOUS_COMPLEXITY_PAIR_ROLES = (
    "responder_extraction_core",
    "a_favoring_compensation",
    "b_favoring_compensation",
    "left_only_selective",
    "right_only_selective",
)
HETEROGENEOUS_COMPLEXITY_PROFILES = (
    (
        "heterogeneous_1",
        (
            (5, 1, 10, 5, 4),
            (3, 4, 25, 7, 23),
            (4, 15, 3, 16, 1),
            (4, 2, 6, 6, 10),
            (4, 12, 7, 8, 2),
        ),
    ),
    (
        "heterogeneous_2",
        (
            (4, 1, 12, 6, 3),
            (4, 2, 22, 4, 20),
            (5, 20, 9, 21, 7),
            (3, 5, 6, 8, 14),
            (2, 10, 7, 5, 2),
        ),
    ),
    (
        "heterogeneous_3",
        (
            (5, 2, 11, 6, 5),
            (5, 7, 20, 10, 19),
            (4, 23, 6, 24, 2),
            (4, 3, 5, 8, 13),
            (4, 8, 9, 3, 1),
        ),
    ),
    (
        "heterogeneous_4",
        (
            (4, 1, 10, 5, 3),
            (3, 3, 18, 7, 15),
            (5, 19, 4, 20, 2),
            (3, 2, 7, 9, 11),
            (5, 8, 11, 1, 8),
        ),
    ),
    (
        "heterogeneous_5",
        (
            (6, 1, 11, 7, 3),
            (2, 8, 28, 10, 24),
            (3, 17, 4, 19, 1),
            (3, 3, 5, 9, 13),
            (3, 12, 10, 6, 5),
        ),
    ),
    (
        "heterogeneous_6",
        (
            (6, 2, 11, 5, 3),
            (4, 7, 17, 9, 16),
            (2, 19, 5, 20, 4),
            (2, 4, 8, 11, 14),
            (5, 12, 9, 7, 2),
        ),
    ),
)

RESPONDER_EXTRACTION_HOLDOUT_PROFILES = (
    ("high_gap_1_9", "near_flat_3_4", (1, 9), (3, 4)),
    ("high_gap_1_10", "near_flat_3_5", (1, 10), (3, 5)),
    ("high_gap_2_10", "near_flat_4_5", (2, 10), (4, 5)),
    ("high_gap_1_12", "near_flat_5_7", (1, 12), (5, 7)),
    ("high_gap_2_12", "near_flat_5_7", (2, 12), (5, 7)),
    ("high_gap_2_11", "near_flat_4_6", (2, 11), (4, 6)),
)
# Current 30-market panel: five quantities x two A profiles x three B profiles.
RESPONDER_EXTRACTION_CROSSED_A_VALUES = (
    ("high_gap_1_11", (1, 11)),
    ("high_gap_2_10", (2, 10)),
)
RESPONDER_EXTRACTION_CROSSED_B_VALUES = (
    ("near_flat_5_7", (5, 7)),
    ("near_flat_4_8", (4, 8)),
    ("near_flat_3_9", (3, 9)),
)

# Role/pressure grid. Every pair sums to 100 for comparable welfare weights.
EXTRACTION_REGION_VALUE_PAIRS = (
    (5, 95),
    (10, 90),
    (20, 80),
    (30, 70),
    (40, 60),
    (45, 55),
)
EXTRACTION_ROLES = ("responder", "proposer", "symmetric")
