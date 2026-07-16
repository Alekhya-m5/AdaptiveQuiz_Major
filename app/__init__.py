import os
from flask import Flask
from .models import db


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    db_path = os.environ.get("DATABASE_URL")
    if not db_path:
        instance_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
        os.makedirs(instance_dir, exist_ok=True)
        db_path = "sqlite:///" + os.path.join(instance_dir, "quiz_system.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_path
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from .routes_common import common_bp
    from .routes_student import student_bp
    from .routes_teacher import teacher_bp

    app.register_blueprint(common_bp)
    app.register_blueprint(student_bp, url_prefix="/student")
    app.register_blueprint(teacher_bp, url_prefix="/teacher")

    with app.app_context():
        db.create_all()

    return app
