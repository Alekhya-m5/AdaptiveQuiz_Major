from flask import Blueprint, render_template, session, redirect, url_for, flash

common_bp = Blueprint("common", __name__)


@common_bp.route("/")
def index():
    if session.get("role") == "student":
        return redirect(url_for("student.dashboard"))
    if session.get("role") == "teacher":
        return redirect(url_for("teacher.dashboard"))
    return render_template("index.html")


@common_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("common.index"))
