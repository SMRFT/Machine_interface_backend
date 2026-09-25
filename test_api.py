import requests

print("Testing /device-data/")
resp = requests.post("http://127.0.0.1:8000/device-data/", json={})
print("Status:", resp.status_code)
print("Response:", resp.json())

print("\nTesting /get-testcode-by-barcode/ (No barcode)")
resp2 = requests.post("http://127.0.0.1:8000/get-testcode-by-barcode/", json={})
print("Status:", resp2.status_code)
print("Response:", resp2.json())

print("\nTesting /get-testcode-by-barcode/ (Dummy barcode)")
resp3 = requests.post("http://127.0.0.1:8000/get-testcode-by-barcode/", json={"barcode": "DUMMY123"})
print("Status:", resp3.status_code)
print("Response:", resp3.json())
