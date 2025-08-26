from django.db import models

from django.db import models
from django.utils import timezone

class Testvalue(models.Model):
    DeviceID = models.CharField(max_length=20)
    lab_unique_id = models.CharField(max_length=25)
    Barcode = models.CharField(max_length=10)
    TestCode = models.CharField(max_length=10)
    Value = models.CharField(max_length=255)
    RawData = models.TextField(null=True, blank=True)

    # CreatedDate should be when record is first created
    CreatedDate = models.DateTimeField(default=timezone.now)

    # Receiveddate will auto-update every time the row is updated
    Receiveddate = models.DateTimeField(auto_now=True)

    # Default to "pending"
    processingstatus = models.CharField(
        max_length=25,
        default="pending",
    )

    # Nullable, set only when processed
    processesdate = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.DeviceID} - {self.TestCode}"
