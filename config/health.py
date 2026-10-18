"""Local launcher readiness; no account data, paths, keys or credentials returned."""
import hashlib
from django.conf import settings
from django.db import connection, DatabaseError
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"service": "campus-lab-demo", "status": "unavailable"}, status=503)
    return JsonResponse({
        "service": "campus-lab-demo", "status": "ready",
        "instance": hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()[:16]
    })
