from django.urls import path, include
from .health import health
urlpatterns = [path("health/", health, name="health"), path("", include("core.urls"))]

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
urlpatterns += staticfiles_urlpatterns()
