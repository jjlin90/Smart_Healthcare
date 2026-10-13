"""Content revisions for authorized staff edits, without a schema migration."""
import hashlib
import json

EDITABLE_FIELDS = ("name", "age", "gender", "allergy_history", "past_medical_history", "current_medications")


def profile_revision(profile) -> str:
    # Include the DB timestamp and stored list encodings. The same snapshot
    # used to build the edit form is compared again by an atomic SQL update.
    snapshot = {field: getattr(profile, field) for field in (*EDITABLE_FIELDS, "updated_at")}
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
