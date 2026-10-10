from django.urls import path
from django.contrib.auth import views as auth
from core import views as v

urlpatterns=[path('login/',auth.LoginView.as_view(template_name='login.html'),name='login'),
    path('logout/',auth.LogoutView.as_view(),name='logout'),path('',v.dashboard),path('labs/',v.labs),
    path('equipment/',v.equipment_list),path('equipment/<int:pk>/',v.equipment_detail),
    path('reservations/',v.reservations),path('reservations/<int:pk>/',v.reservation_detail),
    path('loans/',v.loans),path('loans/<int:pk>/',v.loan_detail),path('incidents/',v.incidents),
    path('incidents/<int:pk>/',v.incident_detail),path('work/',v.work),path('work/<int:pk>/',v.work_detail),
    path('notifications/',v.notifications),path('audits/',v.audits),path('users/',v.users),
    path('foundation/',v.foundation),path('report/',v.report),path('act/<str:operation>/<int:pk>/',v.action)]
handler403='core.views.error403'
handler404='core.views.error404'
handler500='core.views.error500'
