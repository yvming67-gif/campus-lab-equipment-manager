from django.urls import path, include
from .health import health
urlpatterns = [path("api/mini/v1/", include("core.miniapi_urls")), path("health/", health, name="health"), path("", include("core.urls"))]

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
urlpatterns += staticfiles_urlpatterns()
