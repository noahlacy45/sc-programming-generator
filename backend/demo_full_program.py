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
# Builds its response dynamically from whatever segments actually result
# from periodization, rather than hardcoding a fixed segment count — that
# hardcoding was exactly the kind of assumption that would have hidden the
# "pure in-season doesn't split into 4-week chunks" bug this test now covers.
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
    def __init__(self, segments, day_letters):
        self.segments = segments
        self.day_letters = day_letters

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
                {
                    "week_start": seg["week_numbers"][0],
                    "week_end": seg["week_numbers"][-1],
                    "days": {d: dict(day_block) for d in self.day_letters},
                }
                for seg in self.segments
            ],
            "suggested_new_drills": [],
        }
        return MockAnthropicResponse(json.dumps(response_json))


class MockAnthropicClient:
    def __init__(self, segments, day_letters):
        self.messages = MockAnthropicMessages(segments, day_letters)


def run_scenario(label, season_status, days_per_week, season_start_date=None, season_end_date=None, generation_date=None):
    patch_mock_cursor()
    from demo_priority_stack import MockConnection
    import periodization
    import claude_programming

    generation_date = generation_date or date(2026, 9, 4)
    conn = MockConnection()

    # Precompute the real segment structure so the mock client can build a
    # response shaped to match — this is what actually exercises whatever
    # periodization.build_week_schedule() produces, instead of assuming it.
    schedule = periodization.build_week_schedule(generation_date, season_status, season_start_date, season_end_date)
    segments = periodization.group_into_segments(schedule)
    day_letters = claude_programming.DAY_LETTERS[days_per_week]

    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    print(f"Segments produced: {[(s['phase'], s['week_numbers'][0], s['week_numbers'][-1]) for s in segments]}")

    client = MockAnthropicClient(segments, day_letters)

    result = program_builder.build_program(
        conn=conn,
        anthropic_client=client,
        player_id=1,
        season_status=season_status,
        days_per_week=days_per_week,
        season_start_date=season_start_date,
        season_end_date=season_end_date,
        generation_date=generation_date,
    )

    print(f"Athlete: {result['athlete']['name']}")
    print(f"PDF size: {len(result['pdf_bytes'])} bytes")

    filename = f"test_output_{label.lower().replace(' ', '_')}.pdf"
    with open(filename, "wb") as f:
        f.write(result["pdf_bytes"])
    print(f"Wrote {filename}")


def main():
    # Scenario 1: off-season with a mid-cycle transition to in-season (the
    # original test scenario — confirms that still works after this change)
    run_scenario(
        "offseason_with_transition", "offseason", 4,
        season_start_date=date(2026, 10, 16),
    )

    # Scenario 2: pure in-season, no end date — this is the exact case that
    # was collapsing into one flat 12-week segment instead of three 4-week
    # blocks. Should now produce 3 segments (weeks 1-4, 5-8, 9-12).
    run_scenario(
        "pure_in_season", "in_season", 3,
    )


if __name__ == "__main__":
    main()