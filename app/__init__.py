import os
from dotenv import load_dotenv
from flask import Flask
from .models import db

# Load environment variables from .env
load_dotenv()


def create_app(test_config=None):
    app = Flask(__name__)

    # Secret Key
    app.config["SECRET_KEY"] = os.getenv(
        "SECRET_KEY",
        "dev-secret-change-in-production"
    )

    # PostgreSQL (Supabase) Connection
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")

    # Disable modification tracking
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    if test_config:
        app.config.update(test_config)

    # Initialize SQLAlchemy
    db.init_app(app)

    # Register Blueprints
    from .routes_common import common_bp
    from .routes_student import student_bp
    from .routes_teacher import teacher_bp

    app.register_blueprint(common_bp)
    app.register_blueprint(student_bp, url_prefix="/student")
    app.register_blueprint(teacher_bp, url_prefix="/teacher")

    # Register CLI Commands
    from .cli import register_cli_commands
    register_cli_commands(app)

    # Create database tables if they don't exist
    if not app.config.get("TESTING") or app.config.get("SQLALCHEMY_DATABASE_URI"):
        with app.app_context():
            db.create_all()
            _run_schema_migrations()
            if not app.config.get("TESTING"):
                from .cli import auto_sync_bundled_assessments
                auto_sync_bundled_assessments()

    return app


def _run_schema_migrations():
    """Ensure database column sizes in PostgreSQL match updated model specifications."""
    try:
        if db.engine.dialect.name == "postgresql":
            with db.engine.connect() as conn:
                conn.execute(db.text("""
                    ALTER TABLE generated_question_records
                        ALTER COLUMN source_page_or_slide TYPE VARCHAR(500),
                        ALTER COLUMN source_filename TYPE VARCHAR(500),
                        ALTER COLUMN option_a TYPE TEXT,
                        ALTER COLUMN option_b TYPE TEXT,
                        ALTER COLUMN option_c TYPE TEXT,
                        ALTER COLUMN option_d TYPE TEXT,
                        ALTER COLUMN topic TYPE VARCHAR(255),
                        ALTER COLUMN concept_tag TYPE VARCHAR(255),
                        ALTER COLUMN target_difficulty TYPE VARCHAR(50),
                        ALTER COLUMN target_bloom TYPE VARCHAR(100),
                        ALTER COLUMN validation_status TYPE VARCHAR(50),
                        ALTER COLUMN reviewed_by TYPE VARCHAR(100);
                """))
                conn.commit()
    except Exception:
        # Ignore if tables do not exist yet or columns already altered
        pass