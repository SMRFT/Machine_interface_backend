from django.urls import path
from . import views
from django.contrib.auth import views as auth_views 

urlpatterns = [

       path('device-data/', views.create_device_data, name='create-device-data'),#Devicedata(machineintreface)
]