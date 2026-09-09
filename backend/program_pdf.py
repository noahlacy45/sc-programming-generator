"""
Renders an assembled S&C program (output of program_builder.build_program)
as a PDF.

Uses fpdf2 — same choice as the mobility webpage's program_pdf.py, and for
the same reason: pure Python, no system-level dependencies to keep the
Cloud Run image simple. Reportlab (used by the Blast report generator) isn't
needed here since this document is tables and narrative text, not charts.

Layout follows the handoff's own Google Sheet column structure directly:
one row per slot, columns for each week's sets/reps side by side — not one
row per week, which would make an off-season block's table 4x longer for
no reason since the exercise itself doesn't change week to week.
"""
from datetime import date
from io import BytesIO

from fpdf import FPDF

from periodization import ALL_SLOTS, SLOT_LABEL

SLOT_COL_WIDTH = 14
EXERCISE_COL_WIDTH = 55
WEEK_COL_WIDTH = 24
NOTES_COL_WIDTH = 30

# fpdf2's core Helvetica font only supports latin-1. Claude's narrative text
# (and potentially drill names/notes) will naturally contain smart-quote
# style Unicode punctuation — sanitize before anything reaches pdf.cell()/
# multi_cell(), rather than assuming input text is already ASCII-safe.
_UNICODE_REPLACEMENTS = {
    "\u2014": " - ",   # em-dash
    "\u2013": "-",     # en-dash
    "\u2018": "'", "\u2019": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"',   # curly double quotes
    "\u2026": "...",   # ellipsis
    "\u2192": "->",    # right arrow (seen in some drill names, e.g. "Sumo Squat -> sumo squat w/ IR")
}


def pdf_safe(text) -> str:
    """Makes any dynamic string safe for fpdf2's core fonts. Applies to
    EVERY piece of text that isn't a hardcoded literal in this file —
    Claude's narrative writing, drill names, priority flag descriptions,
    athlete names — anything that could contain non-latin1 characters."""
    if text is None:
        return ""
    text = str(text)
    for bad, good in _UNICODE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    # Final safety net: drop anything still outside latin-1 rather than crash
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def safe_multi_cell(pdf, w, h, text):
    """
    multi_cell(w=0, ...) in this fpdf2 version leaves the X cursor at the
    RIGHT margin after finishing, rather than resetting to the left margin.
    A following multi_cell(w=0, ...) call then computes its available width
    as (page width - right margin - current X) — which is ~0, and raises
    "Not enough horizontal space to render a single character." Always
    reset X after every multi_cell call so this can't recur.
    """
    pdf.multi_cell(w, h, text)
    pdf.set_x(pdf.l_margin)


class ProgramPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, "RBI HEAT - S&C Program", ln=True, align="C")
        self.set_font("Helvetica", "", 10)
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _athlete_header(pdf: ProgramPDF, athlete: dict, season_status: str, generated_date: date):
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, pdf_safe(athlete["name"]), ln=True)
    pdf.set_font("Helvetica", "", 10)
    meta = f"{athlete.get('position_type', 'unknown').title()}"
    if athlete.get("bats"):
        meta += f"  |  Bats: {athlete['bats'].title()}"
    if athlete.get("throws"):
        meta += f"  |  Throws: {athlete['throws'].title()}"
    meta += f"  |  {season_status.replace('_', '-').title()}  |  Generated {generated_date.strftime('%B %d, %Y')}"
    pdf.cell(0, 6, pdf_safe(meta), ln=True)
    pdf.ln(4)


def _staleness_note(pdf: ProgramPDF, staleness_warning: str | None):
    if not staleness_warning:
        return
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(200, 120, 0)
    safe_multi_cell(pdf, 0, 5, pdf_safe(f"Note: {staleness_warning}"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def _assessment_summary(pdf: ProgramPDF, summary: dict):
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Assessment Summary", ln=True)
    pdf.set_font("Helvetica", "", 9.5)

    for key, title in [
        ("imtp_paragraph", "IMTP"), ("hop_paragraph", "Hop Test"),
        ("cmj_paragraph", "Countermovement Jump"), ("sj_paragraph", "Squat Jump"),
    ]:
        text = summary.get(key)
        if text:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.cell(0, 5, title, ln=True)
            pdf.set_font("Helvetica", "", 9.5)
            safe_multi_cell(pdf, 0, 5, pdf_safe(text))
            pdf.ln(1)

    if summary.get("overall_paragraph"):
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.cell(0, 5, "Overall Athletic Assessment", ln=True)
        pdf.set_font("Helvetica", "", 9.5)
        safe_multi_cell(pdf, 0, 5, pdf_safe(summary["overall_paragraph"]))
    pdf.ln(4)


def _priority_flags(pdf: ProgramPDF, flags: list[dict]):
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Priority Stack", ln=True)
    pdf.set_font("Helvetica", "", 9.5)
    if not flags:
        pdf.cell(0, 6, "No flags on the most recent testing.", ln=True)
    else:
        for f in flags:
            safe_multi_cell(pdf, 0, 5, pdf_safe(f"[Priority {f['priority']}] {f['description']}"))
    pdf.ln(4)


def _segment_table(pdf: ProgramPDF, segment: dict, day_letter: str, day_data: dict, week_labels: list[str]):
    """
    day_data: {slot_code: {"drill_name": str, "video_link": str|None,
                            "weeks": {week_label: {"sets":..,"reps":..}}}}
    """
    is_in_season = week_labels == ["Maintain"]

    pdf.set_font("Helvetica", "B", 11)
    weeks = segment["week_numbers"]
    pdf.cell(0, 7, f"Weeks {weeks[0]}-{weeks[-1]} - Day {day_letter}", ln=True)

    if is_in_season:
        pdf.set_font("Helvetica", "I", 8)
        safe_multi_cell(pdf, 0, 4.5, "In-season: hold prior off-season working loads. Do not chase new PRs during the season.")
        pdf.ln(1)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(SLOT_COL_WIDTH, 6, "Slot", border=1, fill=True)
    pdf.cell(EXERCISE_COL_WIDTH, 6, "Exercise", border=1, fill=True)
    for label in week_labels:
        pdf.cell(WEEK_COL_WIDTH, 6, label, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for slot_code in ALL_SLOTS:
        row = day_data.get(slot_code, {})
        pdf.cell(SLOT_COL_WIDTH, 6, slot_code, border=1)
        exercise_name = pdf_safe(row.get("drill_name") or "-")[:32]
        pdf.cell(EXERCISE_COL_WIDTH, 6, exercise_name, border=1)
        for label in week_labels:
            wk = row.get("weeks", {}).get(label, {})
            sets = wk.get("sets")
            if sets is None:
                cell_text = "-"
            elif is_in_season:
                cell_text = f"{sets} sets"  # full "reps" text is the caption above, not the cell
            else:
                cell_text = pdf_safe(f"{sets}x{wk.get('reps')}")
            pdf.cell(WEEK_COL_WIDTH, 6, cell_text, border=1, align="C")
        pdf.ln()
    pdf.ln(4)


def render_program_pdf(
    athlete: dict,
    season_status: str,
    generated_date: date,
    staleness_warning: str | None,
    assessment_summary: dict,
    priority_flags: list[dict],
    segments_rendered: list[dict],
    day_letters: list[str],
) -> bytes:
    """
    segments_rendered: list of {
        "week_numbers": [...], "phase": ..., "block_number": ...,
        "days": {day_letter: {slot_code: {"drill_name", "video_link", "weeks": {week_label: {"sets","reps"}}}}}
    }
    """
    pdf = ProgramPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    _athlete_header(pdf, athlete, season_status, generated_date)
    _staleness_note(pdf, staleness_warning)
    _assessment_summary(pdf, assessment_summary)
    _priority_flags(pdf, priority_flags)

    for segment in segments_rendered:
        weeks = segment["week_numbers"]
        week_labels = [f"W{w}" for w in weeks] if segment["phase"] == "offseason" else ["Maintain"]
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        phase_title = (
            f"Block {segment['block_number']} - weeks {weeks[0]}-{weeks[-1]}"
            if segment["phase"] == "offseason" else f"In-Season Maintenance - weeks {weeks[0]}-{weeks[-1]}"
        )
        pdf.cell(0, 8, phase_title, ln=True)
        pdf.ln(2)
        for day_letter in day_letters:
            day_data = segment["days"].get(day_letter, {})
            _segment_table(pdf, segment, day_letter, day_data, week_labels)

    # Video link reference list, since fpdf2 table cells above are too narrow for full URLs
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Video Reference Links", ln=True)
    pdf.set_font("Helvetica", "", 8.5)
    seen = set()
    for segment in segments_rendered:
        for day_data in segment["days"].values():
            for slot_code, row in day_data.items():
                name = row.get("drill_name")
                link = row.get("video_link")
                if name and link and name not in seen:
                    seen.add(name)
                    safe_multi_cell(pdf, 0, 5, pdf_safe(f"{name}: {link}"))

    output = pdf.output(dest="S")
    return bytes(output)
