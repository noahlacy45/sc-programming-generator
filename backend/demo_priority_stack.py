"""
Local demo/test harness — no database, no credentials needed.

Runs the ACTUAL production code in vald_analysis.py and periodization.py
against a small in-memory mock of what VALD_FD_* + sc_metric_norms look
like, using a fake DB connection that understands just the query patterns
those modules issue. This is how you sanity-check the computation logic
changes before ever wiring up a real Cloud SQL connection.

Run: python demo_priority_stack.py
"""
from datetime import date

import vald_analysis
import periodization

# =============================================================================
# Mock data — one athlete, crafted to trigger every Priority Stack flag type
# so you can see the full range of output in one run.
# =============================================================================
ATHLETE_ID = "demo-athlete-1"

MOCK_TABLES = {
    "VALD_FD_IMTP": [
        # Previous test (3 weeks ago)
        {
            "testId": "imtp-prev", "trialId": "t1", "trial_number": 1, "athleteId": ATHLETE_ID,
            "athleteName": "Demo Athlete", "recordedEST": "2026-08-14",
            "Peak Vertical Force": 2400.0, "Peak Vertical Force (Left)": 1250.0,
            "Peak Vertical Force (Right)": 1150.0, "Peak Vertical Force Asym (%)": 8.0,
            "RFD - 100ms": 3200.0, "RFD - 100ms (Left)": None, "RFD - 100ms (Right)": None, "RFD - 100ms Asym (%)": None,
            "RFD - 150ms": 4100.0, "RFD - 150ms (Left)": 2150.0, "RFD - 150ms (Right)": 1950.0, "RFD - 150ms Asym (%)": 9.8,
            "Peak Vertical Force / BM": 32.0,
        },
        # Latest test (this week) — RFD and peak force both declined, and
        # asymmetry on peak force is now over 15%
        {
            "testId": "imtp-latest", "trialId": "t2", "trial_number": 1, "athleteId": ATHLETE_ID,
            "athleteName": "Demo Athlete", "recordedEST": "2026-09-04",
            "Peak Vertical Force": 2250.0, "Peak Vertical Force (Left)": 1280.0,
            "Peak Vertical Force (Right)": 970.0, "Peak Vertical Force Asym (%)": 24.0,
            "RFD - 100ms": 3000.0, "RFD - 100ms (Left)": None, "RFD - 100ms (Right)": None, "RFD - 100ms Asym (%)": None,
            "RFD - 150ms": 3600.0, "RFD - 150ms (Left)": 2100.0, "RFD - 150ms (Right)": 1500.0, "RFD - 150ms Asym (%)": 16.7,
            "Peak Vertical Force / BM": 30.0,
        },
    ],
    "VALD_FD_CMJ": [
        {
            "testId": "cmj-latest", "trialId": "t3", "trial_number": 1, "athleteId": ATHLETE_ID,
            "athleteName": "Demo Athlete", "recordedEST": "2026-09-04",
            "Peak Power / BM": 55.0, "Jump Height (Flight Time)": 38.5, "Jump Height (Imp-Mom)": 37.9,
            "Eccentric Duration": 0.35, "Concentric Duration": 0.28,
            "Eccentric Braking RFD": 4800.0, "Eccentric Braking RFD (Left)": 2500.0,
            "Eccentric Braking RFD (Right)": 2300.0, "Eccentric Braking RFD Asym (%)": 8.3,
            "RSI-modified": 0.25,  # below the 0.30 fixed threshold -> Priority 4 flag
            "Concentric Impulse": 210.0, "Concentric Impulse (Left)": 108.0,
            "Concentric Impulse (Right)": 102.0, "Concentric Impulse Asym (%)": 5.7,
            "Eccentric Peak Power / BM": 40.0,
            "Jump Height (FT) Relative Peak Landing Force": 92.0,  # below 100 -> feeds Priority 6
            "Jump Height (FT) Relative Peak Landing Force (Left)": 48.0,
            "Jump Height (FT) Relative Peak Landing Force (Right)": 44.0,
            "Jump Height (FT) Relative Peak Landing Force Asym (%)": 8.7,
            "Eccentric Acceleration Phase Duration": 0.18,
        },
    ],
    "VALD_FD_SJ": [
        {
            "testId": "sj-latest", "trialId": "t4", "trial_number": 1, "athleteId": ATHLETE_ID,
            "athleteName": "Demo Athlete", "recordedEST": "2026-09-04",
            "Peak Power / BM": 50.0, "Jump Height (Flight Time)": 35.0,
            "Concentric RFD": 3900.0, "Concentric RFD (Left)": 2000.0, "Concentric RFD (Right)": 1900.0,
            "Concentric RFD Asym (%)": 5.1,
            "Concentric Impulse": 195.0, "Concentric Impulse (Left)": 98.0, "Concentric Impulse (Right)": 97.0,
            "Concentric Impulse Asym (%)": 1.0,
            "Jump Height (FT) Relative Peak Landing Force": 85.0,  # below 100 -> Priority 3 flag
            "Jump Height (FT) Relative Peak Landing Force (Left)": 44.0,
            "Jump Height (FT) Relative Peak Landing Force (Right)": 41.0,
            "Jump Height (FT) Relative Peak Landing Force Asym (%)": 7.0,
            "Landing Impulse": 180.0, "Landing Impulse (Left)": 91.0, "Landing Impulse (Right)": 89.0,
            "Landing Impulse Asym (%)": 2.2,
        },
    ],
    "VALD_FD_HJ": [
        {
            "testId": "hj-latest", "trialId": "t5", "trial_number": 1, "athleteId": ATHLETE_ID,
            "athleteName": "Demo Athlete", "recordedEST": "2026-09-04",
            "Best RSI (Jump Height/Contact Time)": 1.8, "Mean RSI (Jump Height/Contact Time)": 1.6,
            "Best Contact Time": 0.18, "Mean Contact Time": 0.20,
            "Best Jump Height (Flight Time)": 32.0, "Mean Jump Height (Flight Time)": 29.0,
            "Best Peak Force": 2100.0, "Best Peak Force (Left)": 1080.0, "Best Peak Force (Right)": 1020.0,
            "Best Peak Force (Asym)": 5.7,
            "Mean Peak Force": 1950.0, "Mean Peak Force (Left)": 1000.0, "Mean Peak Force (Right)": 950.0,
            "Mean Peak Force (Asym)": 5.1,
        },
        {
            "testId": "hj-latest", "trialId": "t6", "trial_number": 2, "athleteId": ATHLETE_ID,
            "athleteName": "Demo Athlete", "recordedEST": "2026-09-04",
            "Best RSI (Jump Height/Contact Time)": 1.9, "Mean RSI (Jump Height/Contact Time)": 1.65,
            "Best Contact Time": 0.17, "Mean Contact Time": 0.19,
            "Best Jump Height (Flight Time)": 33.0, "Mean Jump Height (Flight Time)": 30.0,
            "Best Peak Force": 2150.0, "Best Peak Force (Left)": 1100.0, "Best Peak Force (Right)": 1050.0,
            "Best Peak Force (Asym)": 4.5,
            "Mean Peak Force": 2000.0, "Mean Peak Force (Left)": 1020.0, "Mean Peak Force (Right)": 980.0,
            "Mean Peak Force (Asym)": 3.9,
        },
    ],
}

MOCK_SC_METRIC_NORMS = [
    {
        "test_type": "CMJ", "metric_column": "RSI-modified", "direction": "higher_better",
        "low_cutoff": 0.30, "high_cutoff": 0.45,
        "citation": "Sole, Suchomel & Stone 2018, NCAA Division I athletes",
        "min_sample_size": 30, "active_source": "fixed_literature",
    }
]


class MockCursor:
    def __init__(self, dictionary=True):
        self._results = []

    def execute(self, sql, params=None):
        params = params or ()
        sql_lower = sql.lower()

        if "select distinct testid, recordedest" in sql_lower:
            table = sql.split("FROM")[1].split("WHERE")[0].strip()
            athlete_id, as_of = params
            rows = [r for r in MOCK_TABLES[table] if r["athleteId"] == athlete_id and r["recordedEST"] <= as_of]
            seen, unique = set(), []
            for r in sorted(rows, key=lambda r: r["recordedEST"], reverse=True):
                if r["testId"] not in seen:
                    seen.add(r["testId"])
                    unique.append({"testId": r["testId"], "recordedEST": r["recordedEST"]})
            self._results = unique[:2]

        elif "select * from vald_fd_hj where testid" in sql_lower:
            test_id = params[0]
            self._results = [r for r in MOCK_TABLES["VALD_FD_HJ"] if r["testId"] == test_id]

        elif "select * from" in sql_lower and "order by" in sql_lower and "limit 1" in sql_lower:
            table = sql.split("FROM")[1].split("WHERE")[0].strip()
            test_id = params[0]
            rows = [r for r in MOCK_TABLES[table] if r["testId"] == test_id]
            metric = sql.split("ORDER BY")[1].split("DESC")[0].strip().strip("`")
            rows.sort(key=lambda r: r.get(metric, 0) or 0, reverse=True)
            self._results = rows[:1]

        elif "select * from sc_metric_norms" in sql_lower:
            self._results = [n for n in MOCK_SC_METRIC_NORMS if n["test_type"] == "CMJ" and n["metric_column"] == "RSI-modified"]

        else:
            raise NotImplementedError(f"Mock doesn't understand this query: {sql}")

    def fetchone(self):
        return self._results[0] if self._results else None

    def fetchall(self):
        return self._results

    def close(self):
        pass


class MockConnection:
    def cursor(self, dictionary=True):
        return MockCursor(dictionary=dictionary)


def main():
    print("=" * 70)
    print("PRIORITY STACK — mock data demo")
    print("=" * 70)

    conn = MockConnection()
    result = vald_analysis.compute_priority_stack(conn, ATHLETE_ID, date(2026, 9, 4))

    print(f"\nAthlete: {result['athlete_id']}   As of: {result['as_of_date']}")
    print(f"\n{len(result['flags'])} flag(s) found, in priority order:\n")
    for flag in result["flags"]:
        print(f"  [Priority {flag['priority']}] {flag['description']}")

    print("\n" + "=" * 70)
    print("PERIODIZATION — 12-week schedule demo (season starts in 6 weeks)")
    print("=" * 70)
    schedule = periodization.build_week_schedule(
        date(2026, 9, 4), "offseason", season_start_date=date(2026, 10, 16)
    )
    for w in schedule:
        phase_desc = (
            f"Block {w['block_number']}, week {w.get('week_in_block')}"
            + (" (DELOAD)" if w["is_deload"] else "")
            if w["phase"] == "offseason" else "IN-SEASON maintenance"
        )
        print(f"  Week {w['week_number']:2d}: {phase_desc}")

    print("\nWeek 4 (deload) prescriptions, all 9 slots:")
    for slot, presc in periodization.get_week_prescriptions(schedule[3]).items():
        print(f"  {slot} ({presc['label']}): {presc['sets']}x{presc['reps']}")


if __name__ == "__main__":
    main()
