from django.db import models

# Create your models here.
class Testvalue(models.Model):
    DeviceID = models.CharField(max_length=20)
    lab_unique_id=models.CharField(max_length=25)
    Barcode = models.CharField(max_length=10)
    TestCode = models.CharField(max_length=10)
    Value = models.CharField(max_length=255)
    RawData = models.TextField(null=True, blank=True)
    CreatedDate = models.DateTimeField()#find the meaning of the date
    Receiveddate= models.DateTimeField(auto_now=True)#default to currentdate
    processingstatus=models.CharField(max_length=25)#default:pending,completed(values),igorned
    processesdate=models.DateTimeField()
    
    def __str__(self):
        return f"{self.DeviceID} - {self.TestCode}"