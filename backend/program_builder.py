"""
Top-level orchestration: given a player, produces the finished PDF bytes plus
everything the caller (main.py) needs to persist (sc_assessments row,
sc_programs row, any drill_suggestions to insert).

Deliberately does NOT touch GCS or the sc_assessments/sc_programs tables
itself — this module's job ends at "here are the bytes and the metadata,"
matching main.py's responsibility for I/O side effects.
"""
from datetime import date, timedelta
from typing import Optional

import vald_analysis
import periodization
import drill_pool
import claude_programming
import program_pdf

STALENESS_THRESHOLD_DAYS = 56  # 8 weeks


def get_athlete(conn, player_id: int) -> dict:
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT Player_Id, norm_name, vald_profileId, position_type, bats, throws
        FROM player_directory
        WHERE Player_Id = %s
        """,
        (player_id,),
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        raise ValueError(f"No player found for player_id {player_id}")
    return {
        "player_id": row["Player_Id"],
        "name": row["norm_name"],
        "vald_athlete_id": row["vald_profileId"],
        "position_type": row["position_type"],
        "bats": row["bats"],
        "throws": row["throws"],
    }


def check_staleness(priority_stack: dict, as_of_date: date) -> Optional[str]:
    """Soft warning only — never blocks generation. See project notes:
    trainer decided old data should warn, not prevent, program creation."""
    stale_types = []
    for test_type, test_info in priority_stack["tests"].items():
        test_date = test_info.get("test_date")
        if test_date is None:
            continue
        days_old = (as_of_date - date.fromisoformat(str(test_date)[:10])).days
        if days_old > STALENESS_THRESHOLD_DAYS:
            stale_types.append(f"{test_type} ({days_old // 7} weeks old)")
    if not stale_types:
        return None
    return f"Most recent test data is older than 8 weeks for: {', '.join(stale_types)}. Consider a fresh test before relying heavily on this program."


def _resolve_drill_lookup(pools: dict[str, list[dict]]) -> dict[int, dict]:
    """drill_id -> {drill, video_link} across all slot pools, for quick lookup."""
    lookup = {}
    for drills in pools.values():
        for d in drills:
            lookup[d["drill_id"]] = {"drill": d["drill"], "video_link": d.get("video_link")}
    return lookup


def _build_segments_rendered(
    segments: list[dict],
    week_schedule: list[dict],
    claude_response: dict,
    drill_lookup: dict[int, dict],
    day_letters: list[str],
) -> list[dict]:
    """
    Merges Claude's per-segment exercise choices with the deterministic
    per-week sets/reps from periodization.py into the structure
    program_pdf.py renders directly.
    """
    weeks_by_number = {w["week_number"]: w for w in week_schedule}
    claude_segments_by_range = {
        (s["week_start"], s["week_end"]): s for s in claude_response.get("segments", [])
    }

    rendered = []
    for seg in segments:
        weeks = seg["week_numbers"]
        key = (weeks[0], weeks[-1])
        claude_seg = claude_segments_by_range.get(key)
        if claude_seg is None:
            raise ValueError(f"Claude's response is missing a segment for weeks {key}")

        days_out = {}
        for day_letter in day_letters:
            claude_day = claude_seg["days"].get(day_letter, {})
            slots_out = {}
            for slot_code, drill_id in claude_day.items():
                drill_info = drill_lookup.get(drill_id, {"drill": f"Unknown (id {drill_id})", "video_link": None})
                weeks_data = {}
                if seg["phase"] == "offseason":
                    for week_num in weeks:
                        week_entry = weeks_by_number[week_num]
                        presc = periodization.get_prescription(slot_code, week_entry["block_number"], week_entry["week_in_block"])
                        weeks_data[f"W{week_num}"] = {"sets": presc["sets"], "reps": presc["reps"]}
                else:
                    presc = periodization.get_in_season_prescription(slot_code)
                    weeks_data["Maintain"] = {"sets": presc["sets"], "reps": presc["reps"]}
                slots_out[slot_code] = {
                    "drill_name": drill_info["drill"],
                    "video_link": drill_info["video_link"],
                    "weeks": weeks_data,
                }
            days_out[day_letter] = slots_out

        rendered.append({
            "week_numbers": weeks,
            "phase": seg["phase"],
            "block_number": seg["block_number"],
            "days": days_out,
        })

    return rendered


def build_program(
    conn,
    anthropic_client,
    player_id: int,
    season_status: str,
    days_per_week: int,
    season_start_date: Optional[date] = None,
    season_end_date: Optional[date] = None,
    generation_date: Optional[date] = None,
) -> dict:
    """
    Returns:
      {
        "pdf_bytes": bytes,
        "athlete": {...},
        "staleness_warning": str | None,
        "priority_stack": {...},          # for the sc_assessments row
        "suggested_new_drills": [...],    # for drill_suggestions inserts
        "filename": str,
      }
    """
    generation_date = generation_date or date.today()
    day_letters = claude_programming.DAY_LETTERS[days_per_week]

    athlete = get_athlete(conn, player_id)
    if not athlete["vald_athlete_id"]:
        raise ValueError(f"Player {athlete['name']} has no vald_profileId on file — can't pull force-plate data.")

    priority_stack = vald_analysis.compute_priority_stack(conn, athlete["vald_athlete_id"], generation_date)
    staleness_warning = check_staleness(priority_stack, generation_date)

    week_schedule = periodization.build_week_schedule(
        generation_date, season_status, season_start_date=season_start_date, season_end_date=season_end_date
    )
    segments = periodization.group_into_segments(week_schedule)

    pools = drill_pool.get_all_slot_pools(conn)
    pool_text = drill_pool.format_pool_for_prompt(pools)

    prompt = claude_programming.build_prompt(athlete, priority_stack, segments, pool_text, days_per_week)
    raw_response = claude_programming.call_claude(anthropic_client, prompt)
    validated = claude_programming.validate_response(raw_response, pools)

    drill_lookup = _resolve_drill_lookup(pools)
    segments_rendered = _build_segments_rendered(segments, week_schedule, validated, drill_lookup, day_letters)

    pdf_bytes = program_pdf.render_program_pdf(
        athlete=athlete,
        season_status=season_status,
        generated_date=generation_date,
        staleness_warning=staleness_warning,
        assessment_summary=validated.get("assessment_summary", {}),
        priority_flags=priority_stack["flags"],
        segments_rendered=segments_rendered,
        day_letters=day_letters,
    )

    filename = f"{athlete['name'].replace(' ', '_')}_SC_Program_{generation_date.isoformat()}.pdf"

    return {
        "pdf_bytes": pdf_bytes,
        "athlete": athlete,
        "staleness_warning": staleness_warning,
        "priority_stack": priority_stack,
        "suggested_new_drills": validated.get("suggested_new_drills", []),
        "filename": filename,
    }
