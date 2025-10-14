

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .serializers import DeviceDataSerializer

@api_view(['POST'])
def create_device_data(request):
    try:
        serializer = DeviceDataSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                'success': True,
                'data': serializer.data,
                'message': 'Device data created successfully.'
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'success': False,
                'errors': serializer.errors,
                'message': 'Validation failed.'
            }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            'success': False,
            'errors': str(e),
            'message': 'An unexpected error occurred.'
        }, status=status.HTTP_200_OK)
    



from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pymongo import MongoClient
from .models import Bidirectional
import json

# MongoDB connection
client = MongoClient("LAB_DB_HOST")
db = client["Diagnostics"]
core_testdetails = db["core_testdetails"]


@csrf_exempt
def get_testdetails_by_barcode(request):
    """
    Receive a barcode → find its test/device info from Bidirectional →
    return all test codes from core_testdetails (parameters or test_code)
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            barcode = data.get("barcode")

            if not barcode:
                return JsonResponse({"error": "Barcode is required"}, status=400)

            # Step 1: Lookup barcode in Bidirectional
            record = Bidirectional.objects.filter(Barcode=barcode).first()
            if not record:
                return JsonResponse({"error": "Barcode not found"}, status=404)

            test_code = record.TestCode
            device_id = record.DeviceID

            # Step 2: Query MongoDB
            test_doc = core_testdetails.find_one(
                {
                    "$or": [
                        {"test_code": test_code},   # Simple test like ALP
                        {"parameters.{}.test_code".format(device_id): {"$exists": True}}  # CBC-like test
                    ]
                }
            )

            if not test_doc:
                return JsonResponse({"error": "No test details found"}, status=404)

            # Step 3: Extract test codes
            test_codes = []

            # Case 1: Single test (flat)
            if "test_code" in test_doc:
                test_codes.append(test_doc["test_code"])

            # Case 2: Parameterized tests (nested)
            if "parameters" in test_doc:
                for dev, param_list in test_doc["parameters"].items():
                    for param in param_list:
                        test_codes.append(param["test_code"])

            # Step 4: Response
            return JsonResponse({
                "barcode": barcode,
                "device_id": device_id,
                "test_codes": test_codes,
                
            }, status=200)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Only POST method allowed"}, status=405)
