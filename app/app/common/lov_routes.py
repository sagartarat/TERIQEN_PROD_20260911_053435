from flask import Blueprint, jsonify, request
from flask_login import login_required
from app.common.db import get_db
from app.masters.services import (
    fetch_cities_by_country,
    fetch_hospitals,
    fetch_place_of_supply_by_country,
    fetch_states_by_country,
)

common_bp = Blueprint("common", __name__)


@common_bp.route("/get-states", methods=["GET"])
@login_required
def get_states():
    country_name = (request.args.get("country_name") or "").strip()
    if not country_name:
        return jsonify([])

    conn = get_db()
    try:
        return jsonify(fetch_states_by_country(conn, country_name))
    finally:
        conn.close()


@common_bp.route("/get-cities", methods=["GET"])
@login_required
def get_cities():
    country_name = (request.args.get("country_name") or "").strip()
    if not country_name:
        return jsonify([])

    conn = get_db()
    try:
        return jsonify(fetch_cities_by_country(conn, country_name))
    finally:
        conn.close()


@common_bp.route("/get-place-of-supply", methods=["GET"])
@login_required
def get_place_of_supply():
    country_name = (request.args.get("country_name") or "").strip()
    if not country_name:
        return jsonify([])

    conn = get_db()
    try:
        return jsonify(fetch_place_of_supply_by_country(conn, country_name))
    finally:
        conn.close()


@common_bp.route("/get-hospitals", methods=["GET"])
@login_required
def get_hospitals():
    conn = get_db()
    try:
        return jsonify(fetch_hospitals(conn))
    finally:
        conn.close()
