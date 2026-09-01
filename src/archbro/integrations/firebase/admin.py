from __future__ import annotations

from functools import lru_cache


class FirebaseAdminUnavailable(RuntimeError):
    """The server could not initialize a Firebase Admin service."""


@lru_cache(maxsize=4)
def get_firebase_admin_app(project_id: str):
    """Return one named Firebase Admin app for a Google Cloud project.

    Use Application Default Credentials / runtime identity and avoid shipping
    service-account JSON in the repository.
    """

    normalized = project_id.strip()
    if not normalized:
        raise FirebaseAdminUnavailable("Firebase project id is required.")
    try:
        import firebase_admin

        app_name = f"archbro-{normalized}"
        try:
            return firebase_admin.get_app(name=app_name)
        except ValueError:
            return firebase_admin.initialize_app(
                options={"projectId": normalized},
                name=app_name,
            )
    except FirebaseAdminUnavailable:
        raise
    except Exception as exc:
        raise FirebaseAdminUnavailable(
            "Firebase Admin could not be initialized."
        ) from exc
