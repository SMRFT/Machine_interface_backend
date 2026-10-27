import os
import json
from datetime import datetime, timedelta
import pymongo
from pymongo import MongoClient
from bson import ObjectId

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from pyauth.auth import HasRoleAndDataPermission

from .serializers import DeviceDataSerializer


# =========================================
# MongoDB Connection
# =========================================
client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
db = client["Diagnostics"]

# =========================================
# Collections
# =========================================
core_hmsbarcode = db["core_hmsbarcode"]
core_barcodetestdetails = db["core_barcodetestdetails"]
core_testdetails = db["core_testdetails"]
core_testvalue = db["core_testvalue"]

# =========================================
# LOG COLLECTIONS
# =========================================
sent_barcode_logs = db["sent_barcode_logs"]
test_execution_logs = db["test_execution_logs"]

# Initialize compound indexes for concurrency and querying
try:
    test_execution_logs.create_index(
        [("barcode", 1), ("test_id", 1), ("test_code", 1), ("execution_count", 1)],
        unique=True
    )
    test_execution_logs.create_index([("barcode", 1), ("test_code", 1)])
    test_execution_logs.create_index([("device_id", 1), ("execution_status", 1)])
    test_execution_logs.create_index([("barcode", 1), ("execution_status", 1)])
except Exception as idx_err:
    print(f"Warning initializing test_execution_logs index: {idx_err}")


# =========================================
# HELPER FUNCTIONS
# =========================================
def normalize_test_id(tid):
    """Normalize test_id to integer if numeric, else trimmed string."""
    if tid is None:
        return ""
    try:
        return int(tid)
    except (ValueError, TypeError):
        return str(tid).strip()


def normalize_code(code):
    """Normalize test_code or device_id for consistent comparisons."""
    if code is None:
        return ""
    return str(code).strip().upper()


def parse_testdetails_field(raw):
    """Safely parse testdetails field if serialized as JSON string or return list."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    elif isinstance(raw, list):
        return raw
    return []


def check_test_execution_and_rerun(barcode, test_id, test_code, barcode_doc_item=None):
    """
    Checks whether a test (barcode, test_id, test_code) has already been executed
    across core_testvalue records and test_execution_logs.
    Also detects any authorized, unconsumed rerun requests (rerun: true).

    Returns:
    {
        "already_executed": bool,
        "rerun_authorized": bool,
        "rerun_metadata": dict or None,
        "latest_log": dict or None,
        "previous_execution_count": int,
        "reason": str
    }
    """
    target_code = normalize_code(test_code)
    target_id = normalize_test_id(test_id)
    clean_barcode = str(barcode).strip()

    executed_in_core = False
    rerun_found = False
    rerun_metadata = None

    # 1. Search ALL core_testvalue records for this barcode (not just latest)
    try:
        core_docs = list(core_testvalue.find({"barcode": clean_barcode}))
        for doc in core_docs:
            doc_id = doc.get("_id")
            items = parse_testdetails_field(doc.get("testdetails"))
            for item in items:
                if normalize_test_id(item.get("test_id")) == target_id:
                    params = item.get("parameters")
                    item_device = item.get("device_id")
                    item_has_rerun = item.get("rerun") in [True, "true", "True", 1]

                    if isinstance(params, list):
                        has_any_param_val = any(
                            isinstance(p, dict) and p.get("value") not in [None, "", "null"]
                            for p in params
                        )
                        param_matched = False
                        for p in params:
                            if isinstance(p, dict):
                                p_code = normalize_code(p.get("test_code") or p.get("name"))
                                if p_code == target_code or target_code in p_code or p_code in target_code:
                                    param_matched = True
                                    if p.get("value") not in [None, "", "null"] or has_any_param_val:
                                        executed_in_core = True
                                    if p.get("rerun") in [True, "true", "True", 1] or item_has_rerun:
                                        rerun_found = True
                                        rerun_metadata = {
                                            "rerun_time": p.get("rerun_time") or item.get("rerun_time"),
                                            "rerun_by": p.get("rerun_by") or item.get("rerun_by"),
                                            "source": "core_testvalue_param",
                                            "doc_id": doc_id,
                                            "device_id": item_device
                                        }
                        if not param_matched and has_any_param_val:
                            executed_in_core = True

                        if item_has_rerun:
                            rerun_found = True
                            rerun_metadata = {
                                "rerun_time": item.get("rerun_time"),
                                "rerun_by": item.get("rerun_by"),
                                "source": "core_testvalue_item",
                                "doc_id": doc_id,
                                "device_id": item_device
                            }
                    else:
                        val = item.get("value")
                        has_val = (val not in [None, "", "null"]) or (item.get("approve") is not None)
                        if has_val:
                            executed_in_core = True
                        if item_has_rerun:
                            rerun_found = True
                            rerun_metadata = {
                                "rerun_time": item.get("rerun_time"),
                                "rerun_by": item.get("rerun_by"),
                                "source": "core_testvalue_item",
                                "doc_id": doc_id,
                                "device_id": item_device
                            }
    except Exception as e:
        print(f"Error querying core_testvalue for barcode {clean_barcode}: {e}")

    # 2. Check if barcode order document (HMS/B2B) explicitly has rerun: true
    if barcode_doc_item and isinstance(barcode_doc_item, dict):
        if barcode_doc_item.get("rerun") in [True, "true", "True", 1]:
            rerun_found = True
            rerun_metadata = rerun_metadata or {
                "rerun_time": barcode_doc_item.get("rerun_time"),
                "rerun_by": barcode_doc_item.get("rerun_by"),
                "source": "barcode_doc",
                "device_id": barcode_doc_item.get("device_id")
            }

    # 3. Search test_execution_logs for previous runs
    latest_log = None
    previous_count = 0
    try:
        query = {
            "barcode": clean_barcode,
            "test_id": target_id,
            "test_code": str(test_code).strip()
        }
        logs = list(test_execution_logs.find(query).sort("execution_count", -1).limit(1))
        if logs:
            latest_log = logs[0]
            previous_count = latest_log.get("execution_count", 1)
    except Exception as e:
        print(f"Error querying test_execution_logs for barcode {clean_barcode}: {e}")

    latest_status = latest_log.get("execution_status") if latest_log else None
    already_executed = executed_in_core or (latest_status == "COMPLETED")

    if already_executed:
        if rerun_found:
            rerun_authorized = True
            reason = f"Authorized rerun requested by {rerun_metadata.get('rerun_by')} at {rerun_metadata.get('rerun_time')}"
        else:
            rerun_authorized = False
            reason = "Test already executed and rerun is not true"
    else:
        rerun_authorized = False
        reason = "Initial execution"

    return {
        "already_executed": already_executed,
        "rerun_authorized": rerun_authorized,
        "rerun_metadata": rerun_metadata,
        "latest_log": latest_log,
        "previous_execution_count": previous_count,
        "reason": reason
    }


def claim_test_execution(
    barcode,
    test_id,
    test_code,
    device_id,
    location_id,
    patient_name,
    execution_type,
    execution_count,
    is_rerun,
    rerun_meta=None,
    request_user=None,
    timeout_seconds=900
):
    """
    Atomically claims a test execution slot using unique compound index
    and concurrency-safe atomic operations.

    Handles:
    - Initial claims (only one device can win)
    - Rerun claims (only one device can claim the rerun)
    - Device retries (same device re-fetching in-progress test)
    - Timeout takeover (if previous device timed out without executing)

    Returns: (bool, str) -> (success, reason)
    """
    clean_barcode = str(barcode).strip()
    target_id = normalize_test_id(test_id)
    clean_code = str(test_code).strip()
    clean_device = str(device_id).strip() if device_id else None
    now = datetime.utcnow()

    # Check for existing log of this execution_count
    existing_log = test_execution_logs.find_one({
        "barcode": clean_barcode,
        "test_id": target_id,
        "test_code": clean_code,
        "execution_count": execution_count
    })

    if existing_log:
        curr_status = existing_log.get("execution_status")
        curr_device = existing_log.get("device_id")
        last_updated = existing_log.get("updated_date") or existing_log.get("created_date") or now

        if curr_status == "COMPLETED":
            return False, "Already completed"

        if curr_status in ["SENT", "IN_PROGRESS", "ACKNOWLEDGED"]:
            # Check if this is the SAME device retrying
            if normalize_code(curr_device) == normalize_code(clean_device):
                test_execution_logs.update_one(
                    {"_id": existing_log["_id"]},
                    {"$set": {"updated_date": now}}
                )
                return True, "Device retry authorized"

            # Check if previous device timed out or failed
            is_timed_out = (now - last_updated).total_seconds() > timeout_seconds
            if is_timed_out or curr_status == "FAILED":
                # Atomic takeover of timed-out / failed execution
                claimed_doc = test_execution_logs.find_one_and_update(
                    {
                        "_id": existing_log["_id"],
                        "execution_status": {"$in": ["SENT", "IN_PROGRESS", "FAILED"]},
                        "updated_date": existing_log.get("updated_date")
                    },
                    {
                        "$set": {
                            "device_id": clean_device,
                            "execution_status": "SENT",
                            "eligibility_reason": f"Claim takeover: previous device {curr_device} timed out/failed",
                            "updated_date": now
                        }
                    },
                    return_document=pymongo.ReturnDocument.AFTER
                )
                if claimed_doc:
                    return True, "Claim takeover successful"
                return False, "Claim conflict during takeover"

            return False, f"Already claimed by device {curr_device} (active)"

        if curr_status == "FAILED":
            # Allow retry of failed execution
            test_execution_logs.update_one(
                {"_id": existing_log["_id"]},
                {
                    "$set": {
                        "device_id": clean_device,
                        "execution_status": "SENT",
                        "updated_date": now
                    }
                }
            )
            return True, "Retry of failed execution authorized"

    # Brand new execution claim -> insert atomically
    rerun_time = rerun_meta.get("rerun_time") if rerun_meta else None
    rerun_by = rerun_meta.get("rerun_by") if rerun_meta else None
    eligibility_reason = (
        f"Authorized rerun requested by {rerun_by} at {rerun_time}"
        if is_rerun else "Initial execution claim"
    )

    log_doc = {
        "barcode": clean_barcode,
        "patient_name": patient_name,
        "test_id": target_id,
        "test_code": clean_code,
        "device_id": clean_device,
        "locationId": location_id,
        "execution_status": "SENT",
        "execution_type": execution_type,
        "execution_count": execution_count,
        "rerun": bool(is_rerun),
        "rerun_time": rerun_time,
        "rerun_by": rerun_by,
        "eligibility_reason": eligibility_reason,
        "requested_time": now,
        "sent_time": now,
        "executed_time": None,
        "created_by": str(request_user) if request_user else None,
        "created_date": now,
        "updated_date": now
    }

    try:
        test_execution_logs.insert_one(log_doc)
        return True, "Claimed successfully"
    except pymongo.errors.DuplicateKeyError:
        return False, "Concurrent claim conflict: claimed by another device"


def consume_rerun_request(barcode, test_id, test_code, device_id, rerun_meta, execution_count):
    """
    Updates core_testvalue (and barcode_doc) to mark the rerun as consumed.
    Preserves all original test details and execution history.
    Appends audit history to ensure the rerun cannot be reused indefinitely.
    """
    clean_barcode = str(barcode).strip()
    target_id = normalize_test_id(test_id)
    target_code = normalize_code(test_code)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Update in core_testvalue
    try:
        core_docs = list(core_testvalue.find({"barcode": clean_barcode}))
        for doc in core_docs:
            modified = False
            items = parse_testdetails_field(doc.get("testdetails"))
            for item in items:
                if normalize_test_id(item.get("test_id")) == target_id:
                    if item.get("rerun") in [True, "true", "True", 1]:
                        item["rerun"] = False
                        item["rerun_consumed"] = True
                        item["rerun_consumed_time"] = now_str
                        item["rerun_consumed_by_device"] = device_id

                        if "history" not in item or not isinstance(item["history"], list):
                            item["history"] = []
                        item["history"].append({
                            "action": "rerun_consumed",
                            "test_code": test_code,
                            "device_id": device_id,
                            "execution_count": execution_count,
                            "rerun_by": rerun_meta.get("rerun_by") if rerun_meta else None,
                            "rerun_time": rerun_meta.get("rerun_time") if rerun_meta else None,
                            "consumed_at": now_str
                        })
                        modified = True

                    params = item.get("parameters")
                    if isinstance(params, list):
                        for p in params:
                            if isinstance(p, dict) and normalize_code(p.get("test_code") or p.get("name")) == target_code:
                                if p.get("rerun") in [True, "true", "True", 1]:
                                    p["rerun"] = False
                                    p["rerun_consumed"] = True
                                    modified = True

            if modified:
                serialized = json.dumps(items) if isinstance(doc.get("testdetails"), str) else items
                core_testvalue.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"testdetails": serialized, "lastmodified_date": datetime.utcnow()}}
                )
    except Exception as e:
        print(f"Error consuming rerun in core_testvalue: {e}")

    # Also update in core_hmsbarcode / core_barcodetestdetails if present
    for col in [core_hmsbarcode, core_barcodetestdetails]:
        try:
            b_doc = col.find_one({"barcode": clean_barcode})
            if b_doc:
                modified = False
                items = parse_testdetails_field(b_doc.get("testdetails"))
                for item in items:
                    if normalize_test_id(item.get("test_id")) == target_id:
                        if item.get("rerun") in [True, "true", "True", 1]:
                            item["rerun"] = False
                            item["rerun_consumed"] = True
                            modified = True
                if modified:
                    serialized = json.dumps(items) if isinstance(b_doc.get("testdetails"), str) else items
                    col.update_one(
                        {"_id": b_doc["_id"]},
                        {"$set": {"testdetails": serialized, "lastmodified_date": datetime.utcnow()}}
                    )
        except Exception as e:
            print(f"Error consuming rerun in barcode order collection: {e}")


# =========================================
# DEVICE DATA INGESTION
# =========================================
@api_view(['POST'])
# @permission_classes([HasRoleAndDataPermission])
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
    


# =========================================
# GET TESTCODE BY BARCODE
# =========================================
@api_view(["POST"])
# @permission_classes([HasRoleAndDataPermission])
def get_testcode_by_barcode(request):

    try:
        data = request.data
        barcode = data.get("barcode")
        device_Id = data.get("device_Id") or data.get("device_id")

        if not barcode:
            return Response({
                "status": "error",
                "message": "barcode is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        # GET BARCODE DOCUMENT (HMS or B2B)
        barcode_doc = core_hmsbarcode.find_one({"barcode": str(barcode).strip()})
        if not barcode_doc:
            barcode_doc = core_barcodetestdetails.find_one({"barcode": str(barcode).strip()})

        if not barcode_doc:
            return Response({
                "status": "error",
                "message": "Barcode not found"
            }, status=status.HTTP_404_NOT_FOUND)

        patient_name = barcode_doc.get("patientname")
        location_id = (
            barcode_doc.get("location_id")
            or barcode_doc.get("locationId")
            or barcode_doc.get("location")
            or "hms"
        )

        raw_testdetails = barcode_doc.get("testdetails", "[]")
        testdetails = parse_testdetails_field(raw_testdetails)

        response_data = []

        # Request user if available
        request_user = (
            request.user.username
            if getattr(request, "user", None) and request.user.is_authenticated
            else None
        )

        for item in testdetails:
            test_id = item.get("test_id")
            test = core_testdetails.find_one({"test_id": test_id})
            if not test:
                continue

            test_name = test.get("test_name")
            main_test_code = test.get("test_code")
            parameters = test.get("parameters")
            device_ids = test.get("device_id", [])

            candidate_results = []

            # CASE 1: PARAMETERIZED TEST -> MULTIPLE DEVICES
            if isinstance(parameters, dict):
                final_device_ids = list(parameters.keys())
                for device, param_list in parameters.items():
                    if isinstance(param_list, list):
                        for param in param_list:
                            candidate_results.append({
                                "device_id": device,
                                "test_code": param.get("test_code")
                            })

            # CASE 2: PARAMETERIZED TEST -> SINGLE DEVICE
            elif isinstance(parameters, list):
                matched_device = next(
                    (d for d in device_ids if normalize_code(d) == normalize_code(device_Id)),
                    None
                ) if device_Id else None
                default_device = matched_device or (device_ids[0] if device_ids else None)
                final_device_ids = device_ids
                for param in parameters:
                    candidate_results.append({
                        "device_id": default_device,
                        "test_code": param.get("test_code")
                    })

            # CASE 3: SINGLE TEST -> SINGLE / MULTIPLE DEVICES
            else:
                final_device_ids = device_ids
                for device in device_ids:
                    candidate_results.append({
                        "device_id": device,
                        "test_code": main_test_code
                    })

            # DEVICE ID FILTER (if requested by a specific device)
            if device_Id:
                candidate_results = [
                    res for res in candidate_results
                    if normalize_code(res.get("device_id")) == normalize_code(device_Id)
                ]
                final_device_ids = [
                    device for device in final_device_ids
                    if normalize_code(device) == normalize_code(device_Id)
                ]
                if not candidate_results:
                    continue

            # =========================================================
            # DUPLICATE PREVENTION, RERUN LOGIC & CONCURRENCY CLAIM
            # =========================================================
            eligible_results = []

            for result in candidate_results:
                target_device = result.get("device_id")
                target_code = result.get("test_code")

                # Check execution history across all devices and rerun flags
                check_res = check_test_execution_and_rerun(
                    barcode=barcode,
                    test_id=test_id,
                    test_code=target_code,
                    barcode_doc_item=item
                )

                already_executed = check_res["already_executed"]
                rerun_authorized = check_res["rerun_authorized"]
                rerun_metadata = check_res["rerun_metadata"]
                previous_count = check_res["previous_execution_count"]

                # Requirement 1: If test already executed and rerun is NOT true, exclude it!
                if already_executed:
                    if not rerun_authorized:
                        continue
                    # Requirement 2: Authorized rerun advances execution count
                    exec_type = "rerun"
                    exec_count = max(previous_count + 1, 2)
                    is_rerun_flag = True
                else:
                    # Initial run or checking current in-progress claim
                    latest_log = check_res.get("latest_log")
                    exec_type = "initial" if previous_count == 0 else (latest_log.get("execution_type", "initial") if latest_log else "initial")
                    exec_count = 1 if previous_count == 0 else previous_count
                    is_rerun_flag = False

                # Requirement 4: Atomic concurrency-safe claim
                claimed, claim_reason = claim_test_execution(
                    barcode=barcode,
                    test_id=test_id,
                    test_code=target_code,
                    device_id=target_device,
                    location_id=location_id,
                    patient_name=patient_name,
                    execution_type=exec_type,
                    execution_count=exec_count,
                    is_rerun=is_rerun_flag,
                    rerun_meta=rerun_metadata,
                    request_user=request_user
                )

                if not claimed:
                    # Concurrency conflict or active on another device -> exclude
                    continue

                # Consume rerun request so it cannot authorize unlimited executions
                if is_rerun_flag and rerun_metadata:
                    consume_rerun_request(
                        barcode=barcode,
                        test_id=test_id,
                        test_code=target_code,
                        device_id=target_device,
                        rerun_meta=rerun_metadata,
                        execution_count=exec_count
                    )

                # Requirement 3: Backward compatible sent_barcode_logs
                sent_barcode_logs.insert_one({
                    "barcode": str(barcode).strip(),
                    "patient_name": patient_name,
                    "test_id": test_id,
                    "device_id": target_device,
                    "test_code": target_code,
                    "created_date": datetime.utcnow()
                })

                eligible_results.append(result)

            # Skip test if no candidate results were eligible/claimed
            if not eligible_results:
                continue

            active_devices = [
                d for d in final_device_ids
                if d in {r.get("device_id") for r in eligible_results}
            ] or list({r.get("device_id") for r in eligible_results if r.get("device_id")})

            response_data.append({
                "test_id": test_id,
                "device_id": active_devices,
                "results": eligible_results
            })

        return Response({
            "status": "success",
            "barcode": str(barcode).strip(),
            "patient_name": patient_name,
            "data": response_data
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            "status": "error",
            "message": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# =========================================
# UPDATE EXECUTION STATUS LIFECYCLE
# =========================================
@api_view(["POST"])
def update_test_execution_status(request):
    """
    Endpoint for devices or middleware to report test execution lifecycle status:
    - ACKNOWLEDGED: Device received and accepted the test
    - IN_PROGRESS: Test is actively running on the device
    - COMPLETED: Test execution succeeded with results
    - FAILED: Test execution failed / rejected
    """
    try:
        data = request.data
        barcode = data.get("barcode")
        test_code = data.get("test_code")
        test_id = data.get("test_id")
        device_id = data.get("device_id") or data.get("device_Id")
        new_status = data.get("status", "").strip().upper()

        valid_statuses = ["REQUESTED", "SENT", "ACKNOWLEDGED", "IN_PROGRESS", "COMPLETED", "FAILED"]
        if new_status not in valid_statuses:
            return Response({
                "status": "error",
                "message": f"Invalid status. Must be one of {valid_statuses}"
            }, status=status.HTTP_400_BAD_REQUEST)

        if not barcode:
            return Response({
                "status": "error",
                "message": "barcode is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        query = {"barcode": str(barcode).strip()}
        if test_code:
            query["test_code"] = str(test_code).strip()
        if test_id:
            query["test_id"] = normalize_test_id(test_id)
        if device_id:
            query["device_id"] = str(device_id).strip()

        now = datetime.utcnow()
        update_fields = {
            "execution_status": new_status,
            "updated_date": now
        }
        if new_status == "COMPLETED":
            update_fields["executed_time"] = now

        res = test_execution_logs.update_many(query, {"$set": update_fields})

        return Response({
            "status": "success",
            "matched_count": res.matched_count,
            "modified_count": res.modified_count,
            "message": f"Execution status updated to {new_status}"
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            "status": "error",
            "message": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)