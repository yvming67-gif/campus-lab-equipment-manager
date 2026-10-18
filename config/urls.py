from django.urls import path, include
urlpatterns = [path("", include("core.urls"))]

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
urlpatterns += staticfiles_urlpatterns()
