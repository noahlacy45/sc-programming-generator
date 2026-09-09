"""
Block periodization + season-phase scheduling for the 12-week S&C cycle.

Design basis: the HEAT handoff's existing 3-block/4-week structure (validated
against current baseball S&C literature — block periodization is the standard
practical model; off-season = build, in-season = maintain ~90% of developed
power/strength rather than continuing to push new intensity). Season-phase
handling follows Driveline's guidance: don't hard-switch training style at
the season boundary — taper into it, aligned to an existing deload week
rather than inventing a separate taper mechanism.

Equipment constraints (Hotel/Travel Day) and season phase (offseason/in-season
volume) are DELIBERATELY independent — a day can be both in-season AND a
hotel day at once. Don't conflate the two when extending this module.
"""
from datetime import date, timedelta
from typing import Optional

WEEKS_PER_BLOCK = 4
BLOCKS_PER_CYCLE = 3
TOTAL_WEEKS = WEEKS_PER_BLOCK * BLOCKS_PER_CYCLE  # 12

# Block N (1-indexed) -> (phase name, lower-body pattern, intensity label)
BLOCK_PATTERN = {
    1: {"phase_name": "Strength Foundation", "lower_pattern": "Squat", "intensity": "Build"},
    2: {"phase_name": "Strength-Speed Bridge", "lower_pattern": "Hinge", "intensity": "Bridge"},
    3: {"phase_name": "Peak Power Expression", "lower_pattern": "Unilateral", "intensity": "Peak"},
}

ALL_SLOTS = ["1a", "1b", "1c", "2a", "2b", "2c", "3a", "3b", "3c"]
SLOT_LABEL = {
    "1a": "Jump/Plyo", "1b": "Med Ball", "1c": "Carry",
    "2a": "Lower Compound", "2b": "Upper Push", "2c": "Upper Pull",
    "3a": "Single Leg", "3b": "Arm Care", "3c": "Core/Rotational",
}

# Off-season rep/set progression by slot category and week-in-block (1-3;
# week 4 is always deload — see get_prescription). Straight from the handoff.
OFFSEASON_PROGRESSION = {
    "1a": [(3, 3), (4, 3), (5, 3)],   # Jump/Plyo: reps stay low, sets climb
    "1b": [(3, 4), (4, 4), (5, 4)],   # Med Ball: same idea
    "1c": [(2, "40yd"), (3, "40yd"), (3, "40yd")],  # Carry: distance-based, sets climb
    "2a": None,  # Lower Compound: block-specific, see COMPOUND_PROGRESSION
    "2b": None,  # Upper Push: same pattern as compound
    "2c": None,  # Upper Pull: same pattern as compound
    "3a": [(3, 8), (3, 8), (4, 6)],   # Single Leg: moderate reps, sets increase
    "3b": [(2, 10), (2, 10), (3, 10)],  # Arm Care: reps flat/slightly increasing
    "3c": [(2, 10), (3, 10), (3, 12)],  # Core: reps or hold time increase
}

# Compound lift progression (slots 2a/2b/2c) is block-specific per the handoff
COMPOUND_PROGRESSION = {
    1: [(3, 8), (3, 6), (4, 5)],
    2: [(3, 6), (4, 5), (4, 4)],
    3: [(4, 4), (4, 3), (5, 3)],
}

DELOAD_SET_REDUCTION = 1   # sets -1 to -2 on deload week
DELOAD_LOAD_PCT_CUT = 0.125  # load -10 to -15%, use midpoint

# In-season: same 9 slots (arm care never drops), volume capped, no new
# intensity progression — hold steady rather than build.
IN_SEASON_DAYS_PER_WEEK_MAX = 2
IN_SEASON_SET_CAP = {
    "1a": 2, "1b": 2, "1c": 2,
    "2a": 2, "2b": 2, "2c": 2,
    "3a": 2, "3b": 3, "3c": 2,  # arm care (3b) intentionally NOT reduced
}


def get_prescription(slot_code: str, block_number: int, week_in_block: int) -> dict:
    """
    Off-season sets/reps for one slot at one point in the cycle.
    week_in_block: 1-4 (4 = deload).
    """
    is_deload = week_in_block == 4
    idx = min(week_in_block, 3) - 1  # deload reuses week-3's prescription, then reduces it

    if slot_code in ("2a", "2b", "2c"):
        sets, reps = COMPOUND_PROGRESSION[block_number][idx]
    else:
        table = OFFSEASON_PROGRESSION[slot_code]
        sets, reps = table[idx]

    if is_deload:
        if isinstance(sets, int):
            sets = max(sets - DELOAD_SET_REDUCTION, 1)
        # 3b Arm Care sets never drop below 2, even on deload (per handoff)
        if slot_code == "3b":
            sets = max(sets, 2)

    return {
        "slot": slot_code,
        "label": SLOT_LABEL[slot_code],
        "sets": sets,
        "reps": reps,
        "is_deload": is_deload,
        "load_note": f"reduce load ~{int(DELOAD_LOAD_PCT_CUT * 100)}%" if is_deload else None,
    }


def get_in_season_prescription(slot_code: str) -> dict:
    """
    In-season: hold steady, don't push new intensity. Volume capped per slot;
    arm care (3b) explicitly excluded from the cut.
    """
    return {
        "slot": slot_code,
        "label": SLOT_LABEL[slot_code],
        "sets": IN_SEASON_SET_CAP[slot_code],
        "reps": "maintain — hold prior off-season working reps, do not chase new PRs",
        "is_deload": False,
        "load_note": "maintain load; the goal is preserving off-season gains through the season, not building",
    }


def build_week_schedule(
    cycle_start_date: date,
    season_status: str,               # 'offseason' or 'in_season'
    season_start_date: Optional[date] = None,  # offseason -> in_season transition
    season_end_date: Optional[date] = None,    # in_season -> offseason transition (mirror case)
) -> list[dict]:
    """
    Returns one entry per week (1-12) with its phase, block number (if
    off-season), and whether it's a deload week.

    Two transition directions are handled, each only relevant to the
    opposite starting status:
      - season_status='offseason' + season_start_date: builds off-season as
        normal through the last deload at/before the season start, then
        switches to in-season maintenance for the rest of the cycle.
      - season_status='in_season' + season_end_date: stays in maintenance
        mode through the last week before the season ends, then restarts a
        fresh off-season Block 1 for the remainder — a simplification of the
        real-world "3-4 weeks active rest, then resume training" pattern;
        revisit if a literal rest period between season-end and Block 1
        turns out to matter in practice.

    If no relevant date is given, or it falls outside this 12-week window,
    the whole cycle just uses whichever status was given, throughout.
    """
    weeks = []

    if season_status == "in_season":
        transition_week = None
        if season_end_date is not None:
            days_out = (season_end_date - cycle_start_date).days
            if 0 <= days_out < TOTAL_WEEKS * 7:
                transition_week = (days_out // 7) + 1

        for week_num in range(1, TOTAL_WEEKS + 1):
            if transition_week is not None and week_num >= transition_week:
                # Restart a fresh off-season block cycle from this point
                offset = week_num - transition_week
                block_number = (offset // WEEKS_PER_BLOCK) + 1
                week_in_block = (offset % WEEKS_PER_BLOCK) + 1
                weeks.append({
                    "week_number": week_num,
                    "phase": "offseason",
                    "block_number": block_number,
                    "week_in_block": week_in_block,
                    "is_deload": week_in_block == 4,
                })
            else:
                weeks.append({"week_number": week_num, "phase": "in_season", "block_number": None, "is_deload": False})
        return weeks

    # season_status == "offseason"
    transition_week = None
    if season_start_date is not None:
        days_out = (season_start_date - cycle_start_date).days
        if 0 <= days_out < TOTAL_WEEKS * 7:
            transition_week = (days_out // 7) + 1

    offseason_cutoff_week = TOTAL_WEEKS
    if transition_week is not None:
        deload_weeks_at_or_before = [w for w in (4, 8, 12) if w <= transition_week]
        offseason_cutoff_week = deload_weeks_at_or_before[-1] if deload_weeks_at_or_before else 0

    for week_num in range(1, TOTAL_WEEKS + 1):
        if week_num <= offseason_cutoff_week:
            block_number = ((week_num - 1) // WEEKS_PER_BLOCK) + 1
            week_in_block = ((week_num - 1) % WEEKS_PER_BLOCK) + 1
            weeks.append({
                "week_number": week_num,
                "phase": "offseason",
                "block_number": block_number,
                "week_in_block": week_in_block,
                "is_deload": week_in_block == 4,
            })
        else:
            weeks.append({"week_number": week_num, "phase": "in_season", "block_number": None, "is_deload": False})

    return weeks


def get_week_prescriptions(week_entry: dict) -> dict[str, dict]:
    """All 9 slots' prescriptions for one week, given its schedule entry."""
    if week_entry["phase"] == "in_season":
        return {slot: get_in_season_prescription(slot) for slot in ALL_SLOTS}
    return {
        slot: get_prescription(slot, week_entry["block_number"], week_entry["week_in_block"])
        for slot in ALL_SLOTS
    }


def group_into_segments(week_schedule: list[dict]) -> list[dict]:
    """
    Groups consecutive weeks that share the same exercise selection into one
    "segment" — an off-season block (weeks sharing phase='offseason' and the
    same block_number), or a contiguous in-season maintenance stretch.
    Exercise CHOICE (Claude's job) happens once per segment; reps/sets
    (already deterministic) still vary week to week within it.
    """
    segments = []
    current = None

    for week in week_schedule:
        key = (week["phase"], week.get("block_number"))
        if current is None or (current["phase"], current["block_number"]) != key:
            if current is not None:
                segments.append(current)
            current = {
                "phase": week["phase"],
                "block_number": week.get("block_number"),
                "week_numbers": [week["week_number"]],
            }
        else:
            current["week_numbers"].append(week["week_number"])

    if current is not None:
        segments.append(current)

    for seg in segments:
        if seg["phase"] == "offseason":
            seg["pattern"] = BLOCK_PATTERN[seg["block_number"]]
        else:
            seg["pattern"] = {"phase_name": "In-Season Maintenance", "lower_pattern": None, "intensity": "Maintain"}

    return segments
