from django.urls import path
from . import views as v
from . import coordination_views as cv
from . import product_views as pv
urlpatterns=[path("schedule/",pv.schedule,name="schedule"),path("",v.dashboard,name="dashboard"),path("login/",v.login_page,name="login"),path("identity/",v.identity,name="identity"),path("auth/callback/",v.callback,name="callback"),path("logout/",v.signout,name="logout"),path("resources/",v.resources,name="resources"),path("resources/<int:pk>/",v.resource_detail,name="resource"),path("bookings/",v.bookings,name="bookings"),path("bookings/<int:pk>/",v.booking,name="booking"),path("bookings/<int:pk>/<str:action>/",v.booking_action,name="booking-action"),path("incidents/",v.incidents,name="incidents"),path("incidents/new/",v.incident_new,name="incident-new"),path("incidents/<int:pk>/",v.incident,name="incident"),path("incidents/<int:pk>/<str:action>/",v.incident_action,name="incident-action"),path("evidence/<int:pk>/",v.evidence_download,name="evidence"),path("work/",v.work_list,name="work"),path("work/<int:pk>/",v.work_detail,name="work-detail"),path("work/<int:pk>/<str:action>/",v.work_action,name="work-action"),path("notifications/",v.notifications,name="notifications"),path("audit/",v.audit_list,name="audit"),path("reports/",v.reports,name="reports"),path("manage/",v.manage,name="manage"),path("manage/<str:action>/",v.manage_form,name="manage-form"),path("manage/<str:action>/<int:pk>/",v.manage_form,name="manage-edit"),path("deliveries/",v.deliveries,name="deliveries")]

urlpatterns += [
    path('operations/booking/<int:pk>/<str:action>/',cv.booking_operation,name='booking-operation'),
    path('operations/calendar/',cv.calendar,name='calendar-exception'),
    path('operations/work/<int:pk>/reassign/',cv.reassign,name='work-reassign'),
    path('operations/incident/<int:pk>/respond/',cv.respond,name='incident-response'),
]

urlpatterns += [path('operations/health/',cv.operations_health,name='operations-health')]
