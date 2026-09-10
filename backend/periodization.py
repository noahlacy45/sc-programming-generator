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
    "3b": [(2, 12), (2, 12), (3, 15)],  # Arm Care: bumped into evidence-supported 12-15 rep range
    "3c": [(2, "30 sec"), (3, "30 sec"), (3, "30 sec")],  # Core: anti-rotation holds, not reps
}

# Compound lift progression is now differentiated per slot, not shared —
# 2a (Lower Compound) tapers to true low-rep strength/power by Block 3,
# matching a standard strength periodization model. 2b (Upper Push) stays
# moderate-rep throughout rather than following lower-body down to 3 reps —
# Gemini's research (and general throwing-athlete practice) flags aggressive
# low-rep/near-maximal overhead-adjacent pressing as something to avoid even
# in the off-season, given anterior shoulder stress. 2c (Upper Pull) stays
# the highest-rep of the three across all blocks — a deliberate, evidence-
# backed difference: pull volume for throwers is dosed for scapular
# stability/posture (roughly a 2:1 pull:push volume ratio), not 1RM-style
# strength, so it never chases low reps the way 2a does even at peak block.
COMPOUND_PROGRESSION = {
    "2a": {  # Lower Compound
        1: [(3, 8), (3, 6), (4, 5)],
        2: [(3, 6), (4, 5), (4, 4)],
        3: [(4, 4), (4, 3), (5, 3)],
    },
    "2b": {  # Upper Push
        1: [(3, 10), (3, 8), (4, 6)],
        2: [(3, 8), (4, 6), (4, 5)],
        3: [(4, 6), (4, 5), (5, 5)],
    },
    "2c": {  # Upper Pull — stays high-rep throughout, never tapers to 2a/2b's low reps
        1: [(4, 12), (4, 10), (5, 10)],
        2: [(4, 10), (5, 10), (5, 8)],
        3: [(5, 10), (5, 8), (5, 8)],
    },
}

DELOAD_SET_REDUCTION = 1   # sets -1 to -2 on deload week
DELOAD_LOAD_PCT_CUT = 0.125  # load -10 to -15%, use midpoint

# In-season: FLAT across weeks 1-3 (identical sets/reps each week), with
# week 4 as the only real change (lighter recovery week). This is a
# deliberate choice, not a default — researched and discussed directly:
# the case for week-to-week undulation in the literature (e.g. lighter
# weeks aligned with heavy-travel stretches, heavier weeks aligned with
# lighter game weeks) doesn't have anything to attach to for this athlete
# population — HS tournament ball (weekend-heavy, repeats most weeks all
# summer/fall) and HS spring season (steady 2-4 games/week) don't have the
# kind of week-to-week schedule variability that undulation is meant to
# respond to. Flat is the more defensible choice here, not a compromise.
IN_SEASON_PROGRESSION = {
    "1a": (2, 3),          # Jump/Plyo — power work, quality over volume
    "1b": (2, 4),          # Med Ball
    "1c": (2, "20yd"),     # Carry
    "2a": (3, 6),          # Lower Compound — the day's main strength driver, gets an extra set
    "2b": (2, 6),          # Upper Push
    "2c": (3, 8),          # Upper Pull — extra set alongside its higher reps, same 2:1 pull:push rationale
    "3a": (2, 6),          # Single Leg
    "3b": (3, 12),         # Arm Care
    "3c": (2, "20 sec"),   # Core/Rotational — anti-rotation hold, not reps
}



def get_prescription(slot_code: str, block_number: int, week_in_block: int) -> dict:
    """
    Off-season sets/reps for one slot at one point in the cycle.
    week_in_block: 1-4 (4 = deload).
    """
    is_deload = week_in_block == 4
    idx = min(week_in_block, 3) - 1  # deload reuses week-3's prescription, then reduces it

    if slot_code in ("2a", "2b", "2c"):
        sets, reps = COMPOUND_PROGRESSION[slot_code][block_number][idx]
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


# Gentle rep decline block-to-block, reflecting cumulative season fatigue —
# a real, distinct concept from the week-to-week undulation question already
# settled: this is a slow trend across a whole season (a player in weeks
# 9-12 carries more accumulated wear than in weeks 1-4), not a response to
# week-to-week schedule variance. Applied to reps only; sets stay governed
# purely by slot category (already differentiated above). Arm Care (3b) is
# excluded — it never backs off, same rule as the recovery-week logic.
BLOCK_FATIGUE_SCALE = {1: 1.0, 2: 0.85, 3: 0.70}


def _scale_value(value, factor: float, floor: int = 2):
    """
    Scales a plain number or a 'N<suffix>' string (e.g. '20yd', '20 sec') by
    `factor`, flooring at `floor` so it never reduces to something silly.
    Shared by both the block-to-block fatigue decline and the within-block
    recovery-week reduction — same parsing problem, different factor.
    """
    if isinstance(value, int):
        return max(round(value * factor), floor)

    import re
    match = re.match(r"^(\d+)(.*)$", value)
    if not match:
        return value  # can't parse it — leave as-is rather than guess
    number, suffix = match.groups()
    return f"{max(round(int(number) * factor), floor)}{suffix}"


def _reduce_reps_for_recovery(reps):
    """Within-block recovery-week reduction — see get_in_season_prescription."""
    factor = 0.6 if isinstance(reps, int) else 0.75
    return _scale_value(reps, factor)


def get_in_season_prescription(slot_code: str, week_in_block: int, block_number: int) -> dict:
    """
    In-season sets/reps for one slot at one point in a 4-week chunk.
    FLAT across weeks 1-3 WITHIN a block (same sets/reps every week) — a
    deliberate choice; see IN_SEASON_PROGRESSION's comment for why. That is
    a different question from whether Block 1, 2, and 3 should look
    identical to each other — they shouldn't, and now don't: reps decline
    gently block to block via BLOCK_FATIGUE_SCALE, reflecting cumulative
    season fatigue, on top of which week 4 of each block still applies its
    own additional within-block recovery reduction.

    Week 4 (recovery) does NOT reduce sets — with an in-season baseline of
    only 2-3 sets to begin with, subtracting even one crashes straight to a
    single set, which reads as "barely a workout" rather than a deliberate
    lighter week. Instead: same sets every week (so it still feels like a
    real, substantive session), reps drop via _reduce_reps_for_recovery(),
    and load_note calls for reduced intensity/effort. Arm Care (3b) is
    exempted from BOTH reductions — sets AND reps stay identical across
    every week and every block, consistent with it never backing off.
    """
    is_recovery_week = week_in_block == 4
    sets, base_reps = IN_SEASON_PROGRESSION[slot_code]

    if slot_code == "3b":
        reps = base_reps
    else:
        scale = BLOCK_FATIGUE_SCALE[min(block_number, 3)]
        reps = _scale_value(base_reps, scale) if scale != 1.0 else base_reps
        if is_recovery_week:
            reps = _reduce_reps_for_recovery(reps)

    return {
        "slot": slot_code,
        "label": SLOT_LABEL[slot_code],
        "sets": sets,
        "reps": reps,
        "is_deload": is_recovery_week and slot_code != "3b",
        "load_note": (
            "arm care never backs off — same dose every week and every block"
            if slot_code == "3b" else
            "lighter recovery week — same sets, fewer reps, drop load/effort to ~60-70%"
            if is_recovery_week else
            "maintain load; the goal is preserving off-season gains through the season, not building"
        ),
    }


def build_week_schedule(
    cycle_start_date: date,
    season_status: str,               # 'offseason' or 'in_season'
    season_start_date: Optional[date] = None,  # offseason -> in_season transition
    season_end_date: Optional[date] = None,    # in_season -> offseason transition (mirror case)
) -> list[dict]:
    """
    Returns one entry per week (1-12) with its phase, block number, and
    whether it's a deload week. block_number/week_in_block are assigned for
    BOTH phases (not just off-season) — in-season time also splits into
    4-week chunks the same way off-season blocks do, so a purely in-season
    12-week cycle still produces three separate 4-week segments (weeks 1-4,
    5-8, 9-12) with their own day A/B/C tables, rather than one flat
    12-week block. This is what lets exercises vary every 4 weeks even
    during the season, not just during an off-season build.

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
    the whole cycle just uses whichever status was given, throughout —
    still split into 4-week chunks either way.
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
                # Still split into 4-week chunks even though every chunk
                # uses the same maintenance prescription — this is what
                # gives Claude a fresh segment to pick different exercises
                # for every 4 weeks, matching how off-season blocks work.
                block_number = ((week_num - 1) // WEEKS_PER_BLOCK) + 1
                week_in_block = ((week_num - 1) % WEEKS_PER_BLOCK) + 1
                weeks.append({
                    "week_number": week_num,
                    "phase": "in_season",
                    "block_number": block_number,
                    "week_in_block": week_in_block,
                    "is_deload": week_in_block == 4,
                })
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
        block_number = ((week_num - 1) // WEEKS_PER_BLOCK) + 1
        week_in_block = ((week_num - 1) % WEEKS_PER_BLOCK) + 1
        if week_num <= offseason_cutoff_week:
            weeks.append({
                "week_number": week_num,
                "phase": "offseason",
                "block_number": block_number,
                "week_in_block": week_in_block,
                "is_deload": week_in_block == 4,
            })
        else:
            # Same 4-week chunking as in-season gets everywhere else — this
            # cutoff always lands on a week divisible by 4 (see
            # offseason_cutoff_week above), so this naturally starts a fresh
            # block boundary rather than a misaligned partial chunk.
            weeks.append({
                "week_number": week_num,
                "phase": "in_season",
                "block_number": block_number,
                "week_in_block": week_in_block,
                "is_deload": week_in_block == 4,
            })

    return weeks


def get_week_prescriptions(week_entry: dict) -> dict[str, dict]:
    """All 9 slots' prescriptions for one week, given its schedule entry."""
    if week_entry["phase"] == "in_season":
        return {slot: get_in_season_prescription(slot, week_entry["week_in_block"], week_entry["block_number"]) for slot in ALL_SLOTS}
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