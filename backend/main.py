"""
S&C Program Generator API.
"""
import base64
import json
import os
from datetime import date, datetime

import anthropic
import mysql.connector
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from google.cloud import storage

load_dotenv()

import secrets_config
import program_builder
import vald_analysis

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": ["*"], "methods": ["GET", "POST", "PUT"], "allow_headers": ["Content-Type"]}})

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "sc-programming-reports")


def get_db_connection():
    return mysql.connector.connect(**secrets_config.get_db_config())


def get_anthropic_client():
    return anthropic.Anthropic(api_key=secrets_config.get_anthropic_api_key())


@app.route("/health", methods=["GET"])
def health_check():
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


@app.route("/api/players/search", methods=["GET"])
def search_players():
    """Typeahead for picking an athlete to generate a program for."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT Player_Id, norm_name, position_type, bats, throws
        FROM player_directory
        WHERE norm_name LIKE %s
        ORDER BY norm_name
        LIMIT 15
        """,
        (f"%{q}%",),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)


@app.route("/api/players/<int:player_id>/position", methods=["PUT"])
def update_player_position(player_id):
    """
    Save position_type/bats/throws when the frontend prompts for them
    because they were missing on file. Only fills what's provided —
    doesn't overwrite existing values with nulls.
    """
    data = request.get_json(force=True)
    fields, params = [], []
    for field in ("position_type", "bats", "throws"):
        if data.get(field):
            fields.append(f"{field} = %s")
            params.append(data[field])
    if not fields:
        return jsonify({"error": "No fields provided"}), 400
    params.append(player_id)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE player_directory SET {', '.join(fields)} WHERE Player_Id = %s", params)
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"player_id": player_id}), 200


@app.route("/api/players/<int:player_id>/assessment-preview", methods=["GET"])
def assessment_preview(player_id):
    """
    Priority Stack + staleness check WITHOUT calling Claude or generating
    anything — lets the frontend show a trainer what's flagged before they
    commit to actually generating a program (and spending an API call).
    """
    conn = get_db_connection()
    try:
        athlete = program_builder.get_athlete(conn, player_id)
        if not athlete["vald_athlete_id"]:
            return jsonify({"error": f"{athlete['name']} has no vald_profileId on file — can't pull force-plate data."}), 400

        priority_stack = vald_analysis.compute_priority_stack(conn, athlete["vald_athlete_id"], date.today())
        staleness_warning = program_builder.check_staleness(priority_stack, date.today())

        most_recent_dates = [
            t["test_date"] for t in priority_stack["tests"].values() if t.get("test_date")
        ]
        most_recent_test_date = max(most_recent_dates) if most_recent_dates else None

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

    return jsonify({
        "athlete": athlete,
        "flags": priority_stack["flags"],
        "most_recent_test_date": str(most_recent_test_date) if most_recent_test_date else None,
        "staleness_warning": staleness_warning,
    })


def _save_priority_flags_snapshot(conn, athlete: dict, priority_stack: dict, generation_date: date) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sc_assessments
            (player_id, assessment_date, imtp_test_id, hj_test_id, sj_test_id, cmj_test_id, priority_flags_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            athlete["player_id"], generation_date,
            priority_stack["tests"]["IMTP"]["test_id"], priority_stack["tests"]["HJ"]["test_id"],
            priority_stack["tests"]["SJ"]["test_id"], priority_stack["tests"]["CMJ"]["test_id"],
            json.dumps(priority_stack["flags"]),
        ),
    )
    assessment_id = cur.lastrowid
    conn.commit()
    cur.close()
    return assessment_id


def _save_program_record(conn, assessment_id: int, player_id: int, days_per_week: int, pdf_path: str) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sc_programs (assessment_id, player_id, days_per_week, program_pdf_path)
        VALUES (%s, %s, %s, %s)
        """,
        (assessment_id, player_id, days_per_week, pdf_path),
    )
    program_id = cur.lastrowid
    conn.commit()
    cur.close()
    return program_id


def _save_drill_suggestions(conn, suggestions: list[dict], athlete_name: str):
    if not suggestions:
        return
    cur = conn.cursor()
    for s in suggestions:
        note = f"Suggested for {athlete_name}, slot {s.get('slot')}, weeks {s.get('week_start')}-{s.get('week_end')}: {s.get('reason', '')}"
        cur.execute(
            """
            INSERT INTO drill_suggestions (suggested_name, example_video_link, notes, source, suggested_by)
            VALUES (%s, %s, %s, 'ai_program_generator', %s)
            """,
            (s["suggested_name"], s.get("example_video_link") or "", note, "Claude (S&C Program Generator)"),
        )
    conn.commit()
    cur.close()


def _upload_to_gcs(pdf_bytes: bytes, filename: str) -> str:
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
    return f"gs://{GCS_BUCKET_NAME}/{filename}"


@app.route("/api/programs", methods=["POST"])
def generate_program():
    data = request.get_json(force=True)
    player_id = data.get("player_id")
    season_status = data.get("season_status")
    days_per_week = data.get("days_per_week")

    if not player_id or season_status not in ("offseason", "in_season") or days_per_week not in (3, 4):
        return jsonify({"error": "player_id, season_status ('offseason'/'in_season'), and days_per_week (3 or 4) are required"}), 400

    season_start_date = date.fromisoformat(data["season_start_date"]) if data.get("season_start_date") else None
    season_end_date = date.fromisoformat(data["season_end_date"]) if data.get("season_end_date") else None

    conn = get_db_connection()
    try:
        client = get_anthropic_client()
        result = program_builder.build_program(
            conn=conn,
            anthropic_client=client,
            player_id=player_id,
            season_status=season_status,
            days_per_week=days_per_week,
            season_start_date=season_start_date,
            season_end_date=season_end_date,
        )

        gcs_path = _upload_to_gcs(result["pdf_bytes"], result["filename"])
        assessment_id = _save_priority_flags_snapshot(conn, result["athlete"], result["priority_stack"], date.today())
        program_id = _save_program_record(conn, assessment_id, player_id, days_per_week, gcs_path)
        _save_drill_suggestions(conn, result["suggested_new_drills"], result["athlete"]["name"])

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        app.logger.exception("Program generation failed")
        return jsonify({"error": f"Program generation failed: {e}"}), 500
    finally:
        conn.close()

    return jsonify({
        "program_id": program_id,
        "filename": result["filename"],
        "staleness_warning": result["staleness_warning"],
        "gcs_path": gcs_path,
        "pdf_base64": base64.b64encode(result["pdf_bytes"]).decode("ascii"),
    }), 201


@app.route("/api/programs", methods=["GET"])
def list_programs():
    """Backs the 'Find Programming' tab — search by athlete name."""
    q = (request.args.get("q") or "").strip()
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    sql = """
        SELECT p.program_id, p.player_id, pd.norm_name AS athlete_name,
               p.days_per_week, p.program_pdf_path, p.created_at
        FROM sc_programs p
        JOIN player_directory pd ON pd.Player_Id = p.player_id
    """
    params = ()
    if q:
        sql += " WHERE pd.norm_name LIKE %s"
        params = (f"%{q}%",)
    sql += " ORDER BY p.created_at DESC"
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return jsonify(rows)


@app.route("/api/programs/<int:program_id>/download", methods=["GET"])
def download_program(program_id):
    """Fetches a previously-generated PDF back out of GCS."""
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT program_pdf_path FROM sc_programs WHERE program_id = %s", (program_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({"error": "Program not found"}), 404

    gcs_path = row["program_pdf_path"]  # gs://bucket/filename
    bucket_name, blob_name = gcs_path.replace("gs://", "").split("/", 1)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    pdf_bytes = blob.download_as_bytes()

    return jsonify({"filename": blob_name, "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii")}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
