"""
Eligible-drill lookup for each S&C slot, pulled from drill_library.

Deliberately simple: one query per slot code, matching the same delimited-tag
pattern already used in drill-library-webpage's browse endpoint (a slot's
LABEL — not its code — can appear anywhere in the semicolon-delimited
sc_programming field). drill_library.sc_programming stores values like
"Jump/Plyo" and "Arm Care" from the original tagging pass, not slot codes
like "1a"/"3b" — those codes are this app's own internal shorthand.
"""
from periodization import ALL_SLOTS, SLOT_LABEL


def get_eligible_drills(conn, slot_code: str) -> list[dict]:
    """
    Every drill_library row tagged with this slot's label in its
    sc_programming field. Returned with just enough info for Claude to
    choose from — drill_id (so the choice is unambiguous and verifiable),
    drill name, category for extra context, and video_link for the PDF's
    reference list later.
    """
    label = SLOT_LABEL[slot_code]
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT drill_id, drill, category, video_link
        FROM drill_library
        WHERE sc_programming = %s
           OR sc_programming LIKE %s
           OR sc_programming LIKE %s
           OR sc_programming LIKE %s
        ORDER BY drill
        """,
        (label, f"{label}; %", f"%; {label}", f"%; {label}; %"),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def get_all_slot_pools(conn) -> dict[str, list[dict]]:
    """Eligible drills for all 9 slots at once — fetched fresh per generation,
    so the pool always reflects the current drill_library, not a stale copy."""
    return {slot: get_eligible_drills(conn, slot) for slot in ALL_SLOTS}


def format_pool_for_prompt(pool: dict[str, list[dict]]) -> str:
    """Human-readable pool listing for the Claude prompt, grouped by slot."""
    lines = []
    for slot in ALL_SLOTS:
        drills = pool.get(slot, [])
        lines.append(f"\n{slot} ({SLOT_LABEL[slot]}):")
        if not drills:
            lines.append("  (no eligible drills currently tagged for this slot)")
        for d in drills:
            lines.append(f"  - [id {d['drill_id']}] {d['drill']} (category: {d['category']})")
    return "\n".join(lines)
