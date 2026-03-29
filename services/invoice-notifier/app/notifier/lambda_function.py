import json
import os
import boto3

def lambda_handler(event, context):
    """Invoice notification handler lambda demo.

    Receives an invoice payload via API Gateway POST /notify and
    publishes a notification (stub implementation).
    """
    body = event.get("body") or "{}"
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid JSON body"}),
            }
    else:
        payload = body

    invoice_id = payload.get("invoice_id", "unknown")

    # Stub: log and return acknowledgement
    print(f"Processing notification for invoice: {invoice_id}")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": f"Notification queued for invoice {invoice_id}"}),
    }
