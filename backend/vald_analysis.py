"""
VALD force-plate analysis: trial aggregation + Priority Stack flag computation.

ASSUMPTIONS TO VALIDATE against real data once this is running:
  - Best-trial selection uses ONE row per test (the trial with the best value
    on that test's primary metric), reporting all of that trial's L/R/asym
    columns together — not independently maxing each column across different
    trials. This is standard sports-science practice but worth confirming
    against how your staff actually reads multi-trial VALD exports.
  - Primary metric per test type (used to pick the "best" trial):
        IMTP -> Peak Vertical Force
        CMJ  -> Jump Height (Flight Time)
        SJ   -> Jump Height (Flight Time)
  - Hop Test (HJ) is the one type the handoff explicitly wants Best AND Mean
    for. Implemented as: BEST = max of each "Best ___" column across trials,
    MEAN = average of each "Mean ___" column across trials. Validate this
    matches how your staff already reads multi-trial Hop Test exports.
"""
from datetime import date, timedelta
from typing import Any, Optional

# Columns lower-magnitude = better (used nowhere yet, but here for completeness
# if a future metric needs it — e.g. Contact Time).
LOWER_IS_BETTER = {"Best Contact Time", "Mean Contact Time"}

PRIMARY_METRIC = {
    "IMTP": "Peak Vertical Force",
    "CMJ": "Jump Height (Flight Time)",
    "SJ": "Jump Height (Flight Time)",
}

TABLE_FOR_TYPE = {
    "IMTP": "VALD_FD_IMTP",
    "HJ": "VALD_FD_HJ",
    "SJ": "VALD_FD_SJ",
    "CMJ": "VALD_FD_CMJ",
}

# Asymmetry columns per test type -> (base metric label, left col, right col, asym col)
ASYMMETRY_COLUMNS = {
    "IMTP": [
        ("Peak Vertical Force", "Peak Vertical Force (Left)", "Peak Vertical Force (Right)", "Peak Vertical Force Asym (%)"),
        ("RFD - 150ms", "RFD - 150ms (Left)", "RFD - 150ms (Right)", "RFD - 150ms Asym (%)"),
    ],
    "CMJ": [
        ("Eccentric Braking RFD", "Eccentric Braking RFD (Left)", "Eccentric Braking RFD (Right)", "Eccentric Braking RFD Asym (%)"),
        ("Concentric Impulse", "Concentric Impulse (Left)", "Concentric Impulse (Right)", "Concentric Impulse Asym (%)"),
        ("Relative Peak Landing Force", "Jump Height (FT) Relative Peak Landing Force (Left)",
         "Jump Height (FT) Relative Peak Landing Force (Right)", "Jump Height (FT) Relative Peak Landing Force Asym (%)"),
    ],
    "SJ": [
        ("Concentric RFD", "Concentric RFD (Left)", "Concentric RFD (Right)", "Concentric RFD Asym (%)"),
        ("Concentric Impulse", "Concentric Impulse (Left)", "Concentric Impulse (Right)", "Concentric Impulse Asym (%)"),
        ("Relative Peak Landing Force", "Jump Height (FT) Relative Peak Landing Force (Left)",
         "Jump Height (FT) Relative Peak Landing Force (Right)", "Jump Height (FT) Relative Peak Landing Force Asym (%)"),
        ("Landing Impulse", "Landing Impulse (Left)", "Landing Impulse (Right)", "Landing Impulse Asym (%)"),
    ],
    "HJ": [
        ("Best Peak Force", "Best Peak Force (Left)", "Best Peak Force (Right)", "Best Peak Force (Asym)"),
        ("Mean Peak Force", "Mean Peak Force (Left)", "Mean Peak Force (Right)", "Mean Peak Force (Asym)"),
    ],
}

ASYMMETRY_FLAG_THRESHOLD_PCT = 15.0
LANDING_FORCE_FLAG_THRESHOLD = 100.0  # N/cm, Squat Jump


def get_latest_and_previous_test(conn, test_type: str, athlete_id: str, as_of_date: date):
    """
    Independently per test type: the most recent test on/before as_of_date,
    and whichever test came immediately before that (for trend comparison).
    Force plates are tested weekly, so "previous" is whatever the last test
    was, not tied to a specific week number.
    """
    table = TABLE_FOR_TYPE[test_type]
    cur = conn.cursor(dictionary=True)
    cur.execute(
        f"""
        SELECT DISTINCT testId, recordedEST
        FROM {table}
        WHERE athleteId = %s AND recordedEST <= %s
        ORDER BY recordedEST DESC
        LIMIT 2
        """,
        (athlete_id, as_of_date.isoformat()),
    )
    rows = cur.fetchall()
    cur.close()
    latest = rows[0] if len(rows) > 0 else None
    previous = rows[1] if len(rows) > 1 else None
    return latest, previous


def aggregate_best_trial(conn, test_type: str, test_id: str) -> Optional[dict]:
    """
    IMTP / CMJ / SJ: return every column from the single trial that scored
    best on that test type's primary metric.
    """
    table = TABLE_FOR_TYPE[test_type]
    metric = PRIMARY_METRIC[test_type]
    cur = conn.cursor(dictionary=True)
    cur.execute(
        f"SELECT * FROM {table} WHERE testId = %s ORDER BY `{metric}` DESC LIMIT 1",
        (test_id,),
    )
    row = cur.fetchone()
    cur.close()
    return row


def aggregate_hop_test(conn, test_id: str) -> Optional[dict]:
    """
    HJ: max of each Best_ column across trials, mean of each Mean_ column
    across trials — see module docstring for why.
    """
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM VALD_FD_HJ WHERE testId = %s", (test_id,))
    trials = cur.fetchall()
    cur.close()
    if not trials:
        return None

    result = {"testId": test_id, "athleteId": trials[0]["athleteId"], "recordedEST": trials[0]["recordedEST"]}
    for col in trials[0].keys():
        if col.startswith("Best "):
            vals = [t[col] for t in trials if t[col] is not None]
            result[col] = max(vals) if vals else None
        elif col.startswith("Mean "):
            vals = [t[col] for t in trials if t[col] is not None]
            result[col] = sum(vals) / len(vals) if vals else None
    return result


def get_aggregated_test(conn, test_type: str, test_id: str) -> Optional[dict]:
    if test_id is None:
        return None
    if test_type == "HJ":
        return aggregate_hop_test(conn, test_id)
    return aggregate_best_trial(conn, test_type, test_id)


def find_asymmetry_flags(test_type: str, aggregated: dict) -> list[dict]:
    """Priority 1: any asymmetry over threshold, with direction (which side is weaker)."""
    flags = []
    if not aggregated:
        return flags
    for label, left_col, right_col, asym_col in ASYMMETRY_COLUMNS.get(test_type, []):
        asym_pct = aggregated.get(asym_col)
        left_val = aggregated.get(left_col)
        right_val = aggregated.get(right_col)
        if asym_pct is None or left_val is None or right_val is None:
            continue
        if abs(asym_pct) > ASYMMETRY_FLAG_THRESHOLD_PCT:
            weaker_side = "left" if left_val < right_val else "right"
            flags.append({
                "priority": 1,
                "test_type": test_type,
                "metric": label,
                "asymmetry_pct": round(asym_pct, 1),
                "weaker_side": weaker_side,
                "description": f"{test_type} {label} asymmetry {abs(asym_pct):.1f}% ({weaker_side} weaker)",
            })
    return flags


def find_rfd_decline_flags(current: dict, previous: dict, test_type: str, metric_col: str) -> Optional[dict]:
    """Priority 2: RFD decline from previous test — a within-athlete trend, no population data needed."""
    if not current or not previous:
        return None
    cur_val = current.get(metric_col)
    prev_val = previous.get(metric_col)
    if cur_val is None or prev_val is None or prev_val == 0:
        return None
    pct_change = (cur_val - prev_val) / prev_val * 100
    if pct_change < 0:
        return {
            "priority": 2,
            "test_type": test_type,
            "metric": metric_col,
            "pct_change": round(pct_change, 1),
            "description": f"{test_type} {metric_col} declined {abs(pct_change):.1f}% from previous test",
        }
    return None


def find_landing_force_flag(sj_aggregated: dict) -> Optional[dict]:
    """Priority 3: Squat Jump relative landing force below fixed threshold."""
    if not sj_aggregated:
        return None
    val = sj_aggregated.get("Jump Height (FT) Relative Peak Landing Force")
    if val is not None and val < LANDING_FORCE_FLAG_THRESHOLD:
        return {
            "priority": 3,
            "test_type": "SJ",
            "metric": "Relative Peak Landing Force",
            "value": val,
            "description": f"Squat Jump relative landing force low ({val:.1f} N/cm, threshold {LANDING_FORCE_FLAG_THRESHOLD})",
        }
    return None


def find_rsi_flag(cmj_aggregated: dict, norm: dict) -> Optional[dict]:
    """
    Priority 4: RSI-modified below threshold. Uses sc_metric_norms — fixed
    literature threshold until the pool has enough of its own data
    (norm['active_source'] / norm['min_sample_size'] control the cutover;
    the pool-percentile branch is intentionally not implemented yet — see
    project notes on the fixed-threshold-first approach).
    """
    if not cmj_aggregated or not norm:
        return None
    val = cmj_aggregated.get("RSI-modified")
    if val is None:
        return None
    if norm["active_source"] == "fixed_literature" and norm.get("low_cutoff") is not None:
        if val < norm["low_cutoff"]:
            return {
                "priority": 4,
                "test_type": "CMJ",
                "metric": "RSI-modified",
                "value": val,
                "threshold": norm["low_cutoff"],
                "source": "fixed_literature",
                "citation": norm.get("citation"),
                "description": f"RSI-modified low ({val:.2f}, below {norm['low_cutoff']} per {norm.get('citation')})",
            }
    # own_pool branch: not yet implemented — build when min_sample_size is reached
    return None


def find_peak_force_regression_flag(current: dict, previous: dict) -> Optional[dict]:
    """Priority 5: IMTP peak force regression — within-athlete trend."""
    return find_rfd_decline_flags(current, previous, "IMTP", "Peak Vertical Force")


def find_output_absorption_gap_flag(jump_test_type: str, aggregated: dict) -> Optional[dict]:
    """
    Priority 6: jump height strong but landing force weak (output/absorption
    gap). First-pass heuristic: jump height present and landing force below
    the same fixed threshold as Priority 3, regardless of how "strong" the
    jump height is in absolute terms — this needs refinement once there's
    enough historical data per athlete to judge "strong" relative to their
    own baseline. Flagged here as a known simplification.
    """
    if not aggregated:
        return None
    jump_height = aggregated.get("Jump Height (Flight Time)")
    landing_force = aggregated.get("Jump Height (FT) Relative Peak Landing Force")
    if jump_height is None or landing_force is None:
        return None
    if landing_force < LANDING_FORCE_FLAG_THRESHOLD:
        return {
            "priority": 6,
            "test_type": jump_test_type,
            "metric": "output_absorption_gap",
            "jump_height": jump_height,
            "landing_force": landing_force,
            "description": (
                f"{jump_test_type} jump height {jump_height:.1f}cm with low landing force "
                f"({landing_force:.1f} N/cm) — output/absorption gap"
            ),
        }
    return None


def compute_priority_stack(conn, athlete_id: str, as_of_date: date) -> dict[str, Any]:
    """
    Full Priority Stack computation for one athlete as of a given date.
    Returns the aggregated test data (for narrative writing) plus every
    flag found, ranked by priority (1 = most urgent, per the handoff).
    """
    tests = {}
    previous_tests = {}
    for test_type in ("IMTP", "HJ", "SJ", "CMJ"):
        latest, previous = get_latest_and_previous_test(conn, test_type, athlete_id, as_of_date)
        tests[test_type] = {
            "test_id": latest["testId"] if latest else None,
            "test_date": latest["recordedEST"] if latest else None,
            "aggregated": get_aggregated_test(conn, test_type, latest["testId"]) if latest else None,
        }
        previous_tests[test_type] = (
            get_aggregated_test(conn, test_type, previous["testId"]) if previous else None
        )

    flags = []
    for test_type in ("IMTP", "HJ", "SJ", "CMJ"):
        flags.extend(find_asymmetry_flags(test_type, tests[test_type]["aggregated"]))

    imtp_rfd_flag = find_rfd_decline_flags(
        tests["IMTP"]["aggregated"], previous_tests["IMTP"], "IMTP", "RFD - 150ms"
    )
    if imtp_rfd_flag:
        flags.append(imtp_rfd_flag)

    landing_force_flag = find_landing_force_flag(tests["SJ"]["aggregated"])
    if landing_force_flag:
        flags.append(landing_force_flag)

    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM sc_metric_norms WHERE test_type = 'CMJ' AND metric_column = 'RSI-modified'")
    rsi_norm = cur.fetchone()
    cur.close()
    rsi_flag = find_rsi_flag(tests["CMJ"]["aggregated"], rsi_norm)
    if rsi_flag:
        flags.append(rsi_flag)

    peak_force_flag = find_peak_force_regression_flag(tests["IMTP"]["aggregated"], previous_tests["IMTP"])
    if peak_force_flag:
        peak_force_flag["priority"] = 5
        peak_force_flag["description"] = peak_force_flag["description"].replace("declined", "regressed")
        flags.append(peak_force_flag)

    for jump_type in ("CMJ", "SJ"):
        gap_flag = find_output_absorption_gap_flag(jump_type, tests[jump_type]["aggregated"])
        if gap_flag:
            flags.append(gap_flag)

    flags.sort(key=lambda f: f["priority"])

    return {
        "athlete_id": athlete_id,
        "as_of_date": as_of_date.isoformat(),
        "tests": tests,
        "flags": flags,
    }
