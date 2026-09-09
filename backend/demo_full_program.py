"""
Full pipeline test — mock DB (VALD + player_directory + drill_library) and a
mock Claude client, no real network calls or credentials needed. Proves
program_builder.build_program() actually produces a valid PDF end to end
before ever touching a real database or spending real API calls.

Run: python demo_full_program.py
"""
import json
from datetime import date

from demo_priority_stack import MOCK_TABLES, MOCK_SC_METRIC_NORMS, ATHLETE_ID

import program_builder

# =============================================================================
# Extend the mock DB with player_directory + drill_library
# =============================================================================
MOCK_PLAYER_DIRECTORY = [
    {"Player_Id": 1, "norm_name": "Jason Peele", "vald_profileId": ATHLETE_ID,
     "position_type": "both", "bats": "right", "throws": "right"},
]

MOCK_DRILL_LIBRARY = [
    {"drill_id": 101, "drill": "Box Jump", "category": "Player Performance", "sc_programming": "Jump/Plyo", "video_link": "https://youtu.be/box-jump"},
    {"drill_id": 102, "drill": "MB Keg Toss", "category": "Hitting Primer; Med Ball", "sc_programming": "Med Ball", "video_link": "https://youtu.be/keg-toss"},
    {"drill_id": 103, "drill": "Farmer Carry", "category": "Player Performance", "sc_programming": "Carry", "video_link": "https://youtu.be/farmer-carry"},
    {"drill_id": 104, "drill": "Back Squat", "category": "Player Performance", "sc_programming": "Lower Compound", "video_link": "https://youtu.be/back-squat"},
    {"drill_id": 110, "drill": "Front Squat", "category": "Player Performance", "sc_programming": "Lower Compound", "video_link": "https://youtu.be/front-squat"},
    {"drill_id": 105, "drill": "Incline DB Bench", "category": "Player Performance", "sc_programming": "Upper Push", "video_link": "https://youtu.be/incline-bench"},
    {"drill_id": 106, "drill": "Chin Ups", "category": "Player Performance", "sc_programming": "Upper Pull", "video_link": "https://youtu.be/chin-ups"},
    {"drill_id": 107, "drill": "RFE Split Squat", "category": "Player Performance", "sc_programming": "Single Leg", "video_link": "https://youtu.be/rfe-split-squat"},
    {"drill_id": 108, "drill": "Shoulder Tube - Throwing Motion", "category": "Arm Care", "sc_programming": "Arm Care", "video_link": "https://youtu.be/shoulder-tube"},
    {"drill_id": 109, "drill": "Pallof Press", "category": "Player Performance", "sc_programming": "Core/Rotational", "video_link": "https://youtu.be/pallof-press"},
]


def patch_mock_cursor():
    """Monkey-patch MockCursor.execute to also understand player_directory
    and drill_library queries, on top of what demo_priority_stack.py's
    version already handles."""
    from demo_priority_stack import MockCursor
    original_execute = MockCursor.execute

    def patched_execute(self, sql, params=None):
        params = params or ()
        sql_lower = sql.lower()

        if "from player_directory" in sql_lower:
            player_id = params[0]
            self._results = [r for r in MOCK_PLAYER_DIRECTORY if r["Player_Id"] == player_id]
            return

        if "from drill_library" in sql_lower:
            slot_code = params[0]
            self._results = [
                d for d in MOCK_DRILL_LIBRARY
                if d["sc_programming"] == slot_code or slot_code in d["sc_programming"].split("; ")
            ]
            return

        return original_execute(self, sql, params)

    MockCursor.execute = patched_execute


# =============================================================================
# Mock Claude client — returns a fixed, valid response instead of calling
# the real API. Picks the FIRST eligible drill for every slot on every day,
# which is enough to prove the pipeline (not Claude's actual judgment).
# =============================================================================
class MockAnthropicContent:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class MockAnthropicResponse:
    def __init__(self, text):
        self.content = [MockAnthropicContent(text)]
        self.stop_reason = "end_turn"


class MockAnthropicMessages:
    def create(self, model, max_tokens, messages):
        slot_to_drill = {
            "1a": 101, "1b": 102, "1c": 103, "2a": 104, "2b": 105,
            "2c": 106, "3a": 107, "3b": 108, "3c": 109,
        }
        day_block = {slot: {"drill_id": drill_id, "note": f"Mock coaching note for slot {slot}"} for slot, drill_id in slot_to_drill.items()}

        response_json = {
            "assessment_summary": {
                "imtp_paragraph": "Jason's IMTP testing shows a notable asymmetry favoring the left side, with peak vertical force 24% higher on the left than right, indicating a durability risk for a two-way player.",
                "hop_paragraph": "Hop test reactive strength was solid and symmetric, with no flags of note.",
                "cmj_paragraph": "CMJ RSI-modified came in below the lower-tier reference threshold, suggesting a tendon stiffness/reactive strength limiter worth addressing.",
                "sj_paragraph": "Squat Jump relative landing force was below the 100 N/cm threshold, pointing to an eccentric control deficit on landing.",
                "overall_paragraph": "Overall, this cycle should prioritize right-side unilateral loading to close the asymmetry gap, reactive/plyometric work to build reactive strength, and eccentric landing control work - all while maintaining Jason's two-way workload demands.",
            },
            "segments": [
                {"week_start": 1, "week_end": 4, "days": {d: dict(day_block) for d in ["A", "B", "C", "D"]}},
                {"week_start": 5, "week_end": 12, "days": {d: dict(day_block) for d in ["A", "B", "C", "D"]}},
            ],
            "suggested_new_drills": [
                {"slot": "2a", "week_start": 1, "week_end": 4, "day": "C",
                 "suggested_name": "Right-Leg-Emphasis Front Squat Variation",
                 "reason": "Pool only has generic squat options; something with an explicit unilateral loading bias would better target the flagged right-side weakness."}
            ],
        }
        return MockAnthropicResponse(json.dumps(response_json))


class MockAnthropicClient:
    def __init__(self):
        self.messages = MockAnthropicMessages()


def main():
    patch_mock_cursor()
    from demo_priority_stack import MockConnection

    conn = MockConnection()
    client = MockAnthropicClient()

    result = program_builder.build_program(
        conn=conn,
        anthropic_client=client,
        player_id=1,
        season_status="offseason",
        days_per_week=4,
        season_start_date=date(2026, 10, 16),
        generation_date=date(2026, 9, 4),
    )

    print("=" * 70)
    print("FULL PIPELINE TEST")
    print("=" * 70)
    print(f"Athlete: {result['athlete']['name']}")
    print(f"Filename: {result['filename']}")
    print(f"PDF size: {len(result['pdf_bytes'])} bytes")
    print(f"Staleness warning: {result['staleness_warning']}")
    print(f"Suggested new drills: {len(result['suggested_new_drills'])}")
    for s in result["suggested_new_drills"]:
        print(f"  - {s['suggested_name']}: {s['reason']}")

    with open("test_output.pdf", "wb") as f:
        f.write(result["pdf_bytes"])
    print("\nWrote test_output.pdf — open it to check the actual rendered layout.")


if __name__ == "__main__":
    main()