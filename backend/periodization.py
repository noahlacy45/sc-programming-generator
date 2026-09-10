"""
Claude integration: turns Priority Stack flags + periodization segments +
eligible drill pools into actual exercise selections and narrative writing.

Deliberately asks for ONE comprehensive response per program generation,
covering every segment/day/slot plus the 5 narrative paragraphs — not one
call per block. Sonnet 5 handles a JSON output this size fine in one shot,
and it avoids the state-management complexity of stitching multiple calls
together (anti-repeat across segments, etc.).
"""
import json
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

Respond with ONLY valid JSON, no other text, in exactly this shape:
{{
  "assessment_summary": {{
    "imtp_paragraph": "...",
    "hop_paragraph": "...",
    "cmj_paragraph": "...",
    "sj_paragraph": "...",
    "overall_paragraph": "..."
  }},
  "segments": [
    {{
      "week_start": <int>, "week_end": <int>,
      "days": {{
        "A": {{
          "1a": {{"drill_id": <int>, "note": "..."}}, "1b": {{"drill_id": <int>, "note": "..."}}, "1c": {{"drill_id": <int>, "note": "..."}},
          "2a": {{"drill_id": <int>, "note": "..."}}, "2b": {{"drill_id": <int>, "note": "..."}}, "2c": {{"drill_id": <int>, "note": "..."}},
          "3a": {{"drill_id": <int>, "note": "..."}}, "3b": {{"drill_id": <int>, "note": "..."}}, "3c": {{"drill_id": <int>, "note": "..."}}
        }},
        "B": {{ ... same 9 slots, same {{"drill_id", "note"}} shape ... }}
      }}
    }}
  ],
  "suggested_new_drills": [
    {{"slot": "2a", "week_start": <int>, "week_end": <int>, "day": "A", "suggested_name": "...", "reason": "..."}}
  ]
}}
"""


def call_claude(client: anthropic.Anthropic, prompt: str) -> dict[str, Any]:
    response = client.messages.create(
        model=MODEL,
        max_tokens=20000,  # off-season worst case (3 blocks x up to 4 days x 9 slots,
        # each needing a drill_id + note, plus 5 narrative paragraphs) can run
        # meaningfully larger than in-season — 8000 was cutting responses off
        # mid-string on the largest scenarios, producing a JSON parse error
        # that looked like corrupted output but was actually just truncation.
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "max_tokens":
        raise ValueError(
            "Claude's response was cut off before finishing (hit the max_tokens limit) — "
            "this happens on larger programs (more days/week, more off-season blocks) and "
            "shows up as a JSON parsing error, not an obviously token-related one. "
            "Raise max_tokens in claude_programming.call_claude() further if this recurs."
        )

    # Don't assume content[0] is the text block — a thinking block (or any
    # other non-text block) can come first, whose .text is None rather than
    # a string, which is exactly what produced the 'NoneType' object has no
    # attribute 'strip' error. Find the actual text block instead.
    text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text" and block.text]
    if not text_blocks:
        raise ValueError(
            f"Claude's response had no usable text block. stop_reason={response.stop_reason!r}, "
            f"content block types={[getattr(b, 'type', type(b).__name__) for b in response.content]!r}"
        )
    text = text_blocks[0].strip()

    # Defensive: strip markdown code fences if the model wraps the JSON anyway
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


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