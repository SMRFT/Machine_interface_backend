from django.urls import path
from . import views
from django.contrib.auth import views as auth_views 

urlpatterns = [

       path('device-data/', views.create_device_data, name='create-device-data'),#Devicedata(machineintreface)
       path('get-testcode-by-barcode/', views.get_testdetails_by_barcode, name='get_testdetails_by_barcode'),#Bidirectional
]