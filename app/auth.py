from functools import wraps
from flask import session, redirect, url_for, flash


def student_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "student":
            flash("Please log in as a student to continue.", "warning")
            return redirect(url_for("student.login"))
        return f(*args, **kwargs)
    return wrapper


def teacher_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "teacher":
            flash("Please log in as a teacher to continue.", "warning")
            return redirect(url_for("teacher.login"))
        return f(*args, **kwargs)
    return wrapper
