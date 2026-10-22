"""
Claude integration: turns Priority Stack flags + periodization segments +
eligible drill pools into actual exercise selections and narrative writing.

Deliberately asks for ONE comprehensive response per program generation,
covering every segment/day/slot plus the 5 narrative paragraphs — not one
call per block. Sonnet 5 handles a JSON output this size fine in one shot,
and it avoids the state-management complexity of stitching multiple calls
together (anti-repeat across segments, etc.).
"""
from typing import Any

import anthropic

MODEL = "claude-sonnet-5"

DAY_LETTERS = {3: ["A", "B", "C"], 4: ["A", "B", "C", "D"]}


def _priority_flags_text(priority_stack: dict) -> str:
    if not priority_stack["flags"]:
        return "No Priority Stack flags on the most recent testing — no red flags to address."
    lines = [f"  [Priority {f['priority']}] {f['description']}" for f in priority_stack["flags"]]
    return "\n".join(lines)


def _segments_text(segments: list[dict], days_per_week: int) -> str:
    day_letters = DAY_LETTERS[days_per_week]
    lines = []
    for seg in segments:
        weeks = seg["week_numbers"]
        lines.append(
            f"\nSegment: weeks {weeks[0]}-{weeks[-1]} — {seg['pattern']['phase_name']}"
            + (f" (lower-body pattern: {seg['pattern']['lower_pattern']})" if seg["pattern"]["lower_pattern"] else "")
        )
        lines.append(f"  Days this segment needs exercise selections for: {', '.join(day_letters)}")
    return "\n".join(lines)


def build_prompt(
    athlete: dict,             # {name, position_type, bats, throws}
    priority_stack: dict,      # from vald_analysis.compute_priority_stack
    segments: list[dict],      # from periodization.group_into_segments
    drill_pools_text: str,     # from drill_pool.format_pool_for_prompt
    days_per_week: int,
) -> str:
    day_letters = DAY_LETTERS[days_per_week]

    return f"""You are building a strength & conditioning program for a baseball athlete, following an established slot-based system. Every training day uses the same 9 slots:
  1a Jump/Plyo, 1b Med Ball, 1c Carry, 2a Lower Compound, 2b Upper Push,
  2c Upper Pull, 3a Single Leg, 3b Arm Care (NEVER skip this slot), 3c Core/Rotational

ATHLETE:
  Name: {athlete['name']}
  Position: {athlete.get('position_type', 'unknown')}
  Bats: {athlete.get('bats', 'unknown')}   Throws: {athlete.get('throws', 'unknown')}

PRIORITY STACK (force-plate findings, ranked most urgent first):
{_priority_flags_text(priority_stack)}

Use these findings to inform exercise choice — e.g. a flagged asymmetry means the weaker side should lead in unilateral (Single Leg / Carry) slots; an Arm Care-relevant flag should shape which Arm Care option you pick, not just that one is present.

TRAINING SEGMENTS ({days_per_week} days/week — days {', '.join(day_letters)}):
{_segments_text(segments, days_per_week)}

For EACH segment, choose ONE exercise per day per slot from the eligible pool below. The exercise stays the same across every week within a segment (only sets/reps change week to week, and that's handled separately — you're only choosing WHICH exercise). Exercises CAN differ by day within a segment (e.g. Day A's 2a could differ from Day C's 2a), but avoid needless repetition — don't pick the identical drill for the same slot on every single day if the pool offers real variety.

IMPORTANT — vary exercises ACROSS segments too, not just across days within one segment. Each off-season block is a new 4-week block with its own training emphasis (see the lower-body pattern named for each segment above) — it should feel like a distinct rotation, not a continuation of the same exact program with only the numbers changing. When the pool for a slot has more than one real option, avoid picking the same exercise for that slot in Block 1, Block 2, and Block 3 — reserve genuine repetition for slots where the pool is too thin to do otherwise (e.g. only one eligible drill exists for that slot).

ELIGIBLE DRILLS PER SLOT (choose ONLY from these by drill_id — do not invent a drill_id):
{drill_pools_text}

If none of the eligible options in a slot are a good fit for this athlete's needs (e.g. nothing addresses a flagged asymmetry, or the pool for a slot is empty), still pick the best available real option so the slot isn't left blank, AND separately note the gap in "suggested_new_drills" — do not put an invented drill directly into the program itself.

For EACH exercise you choose, also write a SHORT coaching note (under 12 words) — a specific technical/intent cue, not a generic restatement of the exercise name. Match the terse style of: "Light load; max intent every rep", "Single-leg hinge; left side emphasis for symmetry", "Retract and depress scap; increase load W2 to W3".

Return your answer by calling the submit_program tool exactly once. Include a "segments" entry for EVERY segment listed above, and within each segment a "days" entry for EVERY day ({', '.join(day_letters)}), each with all 9 slots filled. Use [] for suggested_new_drills if you have none.
"""


SLOT_CODES = ["1a", "1b", "1c", "2a", "2b", "2c", "3a", "3b", "3c"]

_SLOT_CHOICE = {
    "type": "object",
    "properties": {
        "drill_id": {"type": "integer"},
        "note": {"type": "string"},
    },
    "required": ["drill_id", "note"],
}

_DAY = {
    "type": "object",
    "properties": {code: _SLOT_CHOICE for code in SLOT_CODES},
    "required": SLOT_CODES,
}

SUBMIT_PROGRAM_TOOL = {
    "name": "submit_program",
    "description": "Submit the completed S&C program: assessment summary paragraphs, per-segment/per-day exercise selections, and any suggested new drills.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assessment_summary": {
                "type": "object",
                "properties": {
                    k: {"type": "string"}
                    for k in ["imtp_paragraph", "hop_paragraph", "cmj_paragraph", "sj_paragraph", "overall_paragraph"]
                },
                "required": ["imtp_paragraph", "hop_paragraph", "cmj_paragraph", "sj_paragraph", "overall_paragraph"],
            },
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "week_start": {"type": "integer"},
                        "week_end": {"type": "integer"},
                        "days": {
                            "type": "object",
                            "properties": {d: _DAY for d in ["A", "B", "C", "D"]},
                            "additionalProperties": _DAY,
                        },
                    },
                    "required": ["week_start", "week_end", "days"],
                },
            },
            "suggested_new_drills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slot": {"type": "string"},
                        "week_start": {"type": "integer"},
                        "week_end": {"type": "integer"},
                        "day": {"type": "string"},
                        "suggested_name": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["slot", "suggested_name", "reason"],
                },
            },
        },
        "required": ["assessment_summary", "segments", "suggested_new_drills"],
    },
}


def call_claude(client: anthropic.Anthropic, prompt: str) -> dict[str, Any]:
    # Forced tool use instead of "respond with JSON" free text: the API hands
    # back the tool input already parsed into a dict, so malformed JSON
    # (shorthand like "{ ... }", trailing commas, comments, stray prose) can no
    # longer surface as a json.loads error.
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        tools=[SUBMIT_PROGRAM_TOOL],
        tool_choice={"type": "tool", "name": "submit_program"},
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "max_tokens":
        raise ValueError(
            "Claude's response was cut off at the max_tokens limit before the full program "
            "was written. Raise max_tokens in claude_programming.call_claude."
        )

    tool_inputs = [
        block.input for block in response.content
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_program"
    ]
    if not tool_inputs or not isinstance(tool_inputs[0], dict):
        raise ValueError(
            f"Claude didn't return a submit_program tool call. stop_reason={response.stop_reason!r}, "
            f"content block types={[getattr(b, 'type', type(b).__name__) for b in response.content]!r}"
        )
    return tool_inputs[0]


def validate_response(parsed: dict, drill_pools: dict[str, list[dict]]) -> dict:
    """
    Confirms every drill_id Claude chose actually exists in the pool it was
    given for that slot — guards against hallucinated IDs before anything
    reaches a PDF. Raises ValueError with a clear message if validation fails,
    rather than silently accepting a bad id.
    """
    valid_ids_by_slot = {
        slot: {d["drill_id"] for d in drills} for slot, drills in drill_pools.items()
    }

    for segment in parsed.get("segments", []):
        for day, slots in segment["days"].items():
            for slot_code, choice in slots.items():
                drill_id = choice.get("drill_id") if isinstance(choice, dict) else choice
                if drill_id not in valid_ids_by_slot.get(slot_code, set()):
                    raise ValueError(
                        f"Claude chose drill_id {drill_id} for slot {slot_code} "
                        f"(week {segment['week_start']}-{segment['week_end']}, day {day}), "
                        f"but that id isn't in the eligible pool for that slot."
                    )

    return parsed