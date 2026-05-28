

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
    



from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from pymongo import MongoClient
from datetime import datetime
import json
from pymongo import MongoClient
import os


# =========================================
# MongoDB Connection
# =========================================
client =  MongoClient(os.getenv("GLOBAL_DB_HOST"))

db = client["Diagnostics"]

# =========================================
# Collections
# =========================================
core_hmsbarcode = db["core_hmsbarcode"]
core_testdetails = db["core_testdetails"]

# =========================================
# LOG COLLECTION
# =========================================
sent_barcode_logs = db["sent_barcode_logs"]


@api_view(["POST"])
def get_testcode_by_barcode(request):

    try:

        data = request.data

        barcode = data.get("barcode")

        # =========================================
        # VALIDATION
        # =========================================
        if not barcode:

            return Response({
                "status": "error",
                "message": "barcode is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        # =========================================
        # GET BARCODE DOCUMENT
        # =========================================
        barcode_doc = core_hmsbarcode.find_one({
            "barcode": barcode
        })

        if not barcode_doc:

            return Response({
                "status": "error",
                "message": "Barcode not found"
            }, status=status.HTTP_404_NOT_FOUND)

        # =========================================
        # PATIENT DETAILS
        # =========================================
        patient_name = barcode_doc.get("patientname")

        # =========================================
        # TEST DETAILS
        # =========================================
        raw_testdetails = barcode_doc.get("testdetails", "[]")

        # STRING → JSON
        testdetails = json.loads(raw_testdetails)

        response_data = []

        # =========================================
        # LOOP TESTS
        # =========================================
        for item in testdetails:

            test_id = item.get("test_id")

            # =========================================
            # GET TEST MASTER
            # =========================================
            test = core_testdetails.find_one({
                "test_id": test_id
            })

            if not test:
                continue

            test_name = test.get("test_name")
            main_test_code = test.get("test_code")

            parameters = test.get("parameters")
            device_ids = test.get("device_id", [])

            results = []

            # =========================================
            # CASE 1
            # PARAMETERIZED TEST → MULTIPLE DEVICE
            # =========================================
            if isinstance(parameters, dict):

                final_device_ids = list(parameters.keys())

                for device, param_list in parameters.items():

                    for param in param_list:

                        result_data = {
                            "device_id": device,
                            "test_code": param.get("test_code")
                        }

                        results.append(result_data)

                        # =========================================
                        # SAVE LOG
                        # =========================================
                        sent_barcode_logs.insert_one({
                            "barcode": barcode,
                            "patient_name": patient_name,
                            "test_id": test_id,
                            "device_id": device,
                            "test_code": param.get("test_code"),
                            "created_date": datetime.utcnow()
                        })

            # =========================================
            # CASE 2
            # PARAMETERIZED TEST → SINGLE DEVICE
            # =========================================
            elif isinstance(parameters, list):

                default_device = (
                    device_ids[0]
                    if device_ids else None
                )

                final_device_ids = device_ids

                for param in parameters:

                    result_data = {
                        "device_id": default_device,
                        "test_code": param.get("test_code")
                    }

                    results.append(result_data)

                    # =========================================
                    # SAVE LOG
                    # =========================================
                    sent_barcode_logs.insert_one({
                        "barcode": barcode,
                        "patient_name": patient_name,
                        "test_id": test_id,
                        "device_id": default_device,
                        "test_code": param.get("test_code"),
                        "created_date": datetime.utcnow()
                    })

            # =========================================
            # CASE 3
            # SINGLE TEST → SINGLE/MULTIPLE DEVICE
            # =========================================
            else:

                final_device_ids = device_ids

                for device in device_ids:

                    result_data = {
                        "device_id": device,
                        "test_code": main_test_code
                    }

                    results.append(result_data)

                    # =========================================
                    # SAVE LOG
                    # =========================================
                    sent_barcode_logs.insert_one({
                        "barcode": barcode,
                        "patient_name": patient_name,
                        "test_id": test_id,
                        "device_id": device,
                        "test_code": main_test_code,
                        "created_date": datetime.utcnow()
                    })

            # =========================================
            # FINAL RESPONSE
            # =========================================
            response_data.append({
                "test_id": test_id,
                "device_id": final_device_ids,
                "results": results
            })

        # =========================================
        # SUCCESS RESPONSE
        # =========================================
        return Response({
            "status": "success",
            "barcode": barcode,
            "patient_name": patient_name,
            "data": response_data
        }, status=status.HTTP_200_OK)

    except Exception as e:

        return Response({
            "status": "error",
            "message": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
