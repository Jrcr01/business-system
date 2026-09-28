"""ERP audit rows go in the SAME audit_logs table as AP (one history for the whole system), with user_email = acting_user() --
the signed-in person, set per request by core.middleware.ActingUserMiddleware."""
from django.db import connection


def log(action: str, entity: str, entity_id: int | None = None, old: str | None = None, new: str | None = None) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) "
            "VALUES (acting_user(), %s, %s, %s, %s, %s)",
            [action, entity, entity_id, old, new],
        )
