from django.urls import path
from . import miniapi as api
urlpatterns = [
    path("auth/login/", api.login), path("auth/logout/", api.logout), path("me/", api.me),
    path("resources/", api.resources), path("resources/<int:pk>/", api.resource_detail),
    path("bookings/", api.bookings), path("bookings/<int:pk>/", api.booking_detail),
    path("bookings/<int:pk>/cancel/", api.cancel), path("notifications/", api.notifications),
    path("notifications/read-all/", api.read_all), path("notifications/<int:pk>/read/", api.read_notification),
]
