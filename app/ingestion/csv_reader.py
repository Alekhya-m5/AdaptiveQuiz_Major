"""
CSV Reader and Initial Schema Validation for Weekly Quiz Results.
"""

import hashlib
import io
from pathlib import Path
import pandas as pd

# Canonical required columns for weekly quiz CSV
REQUIRED_COLUMNS = [
    "Student Name",
    "Student ID / USN",
    "Quiz",
    "Subject",
    "Attempt",
    "Question No",
    "Question ID",
    "Concept Tag",
    "Bloom's Level",
    "Question",
    "Option A",
    "Option B",
    "Option C",
    "Option D",
    "Selected Option",
    "Correct Option",
    "Question Marks",
    "Marks Obtained",
    "Submitted At",
]


def compute_file_hash(file_input) -> str:
    """
    Compute SHA-256 hash of a file path or file-like object.
    """
    sha = hashlib.sha256()
    if isinstance(file_input, (str, Path)):
        with open(file_input, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
    elif hasattr(file_input, "read"):
        current_pos = file_input.tell() if hasattr(file_input, "tell") else None
        if hasattr(file_input, "seek"):
            file_input.seek(0)
        while True:
            chunk = file_input.read(65536)
            if not chunk:  # Handles both "" and b"" cleanly!
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            sha.update(chunk)
        if current_pos is not None and hasattr(file_input, "seek"):
            file_input.seek(current_pos)
    elif isinstance(file_input, (bytes, bytearray)):
        sha.update(file_input)
    else:
        raise ValueError(f"Unsupported file input type for hashing: {type(file_input)}")
    return sha.hexdigest()


def read_weekly_csv(file_input):
    """
    Read and validate the weekly quiz CSV.

    Parameters
    ----------
    file_input : str, Path, or file-like object
        File path or stream to read.

    Returns
    -------
    pandas.DataFrame
        Raw validated dataframe with trimmed column names.

    Raises
    ------
    ValueError
        If the file cannot be read, is empty, or lacks required columns.
    """
    try:
        if hasattr(file_input, "seek"):
            file_input.seek(0)
        df = pd.read_csv(file_input)
    except Exception as e:
        raise ValueError(f"Unable to read CSV file: {e}")

    if df.empty:
        raise ValueError("CSV file contains no data.")

    # Remove accidental leading/trailing whitespace from column names
    df.columns = df.columns.str.strip()

    missing_columns = [
        col for col in REQUIRED_COLUMNS if col not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"CSV is missing {len(missing_columns)} required column(s): {', '.join(missing_columns)}. "
            f"Found columns: {list(df.columns)}"
        )

    return df