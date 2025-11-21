import os
import json
import uuid
import time
import logging
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "users")

# ---------- Logging setup ----------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        base = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        if hasattr(record, "extra"):
            base.update(record.extra)
        return json.dumps(base, default=str)

logger = logging.getLogger("user-writer")
logger.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))

# Ensure a single StreamHandler with JSON formatter
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    logger.addHandler(h)
    logger.propagate = False
# -----------------------------------

ddb = boto3.resource("dynamodb")
table = ddb.Table(TABLE_NAME)

def _response(status, body, headers=None):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **(headers or {})},
        "body": json.dumps(body),
    }

def _parse_payload(event):
    """Supports direct invoke and Lambda Function URL / API Gateway events."""
    if isinstance(event, dict) and "body" in event:
        body_raw = event.get("body")
        if event.get("isBase64Encoded"):
            import base64
            body_raw = base64.b64decode(body_raw).decode("utf-8")
        return json.loads(body_raw or "{}")
    return event if isinstance(event, dict) else {}

def handler(event, context):
    start = time.time()
    request_id = getattr(context, "aws_request_id", str(uuid.uuid4()))
    function_name = getattr(context, "function_name", None)

    # Log the incoming request at DEBUG (payload contents) and INFO (metadata)
    logger.info("request_received", extra={"extra": {
        "request_id": request_id,
        "function_name": getattr(context, "function_name", None),
        "function_version": getattr(context, "function_version", None),
        "cold_start": os.getenv("AWS_EXECUTION_ENV", "").endswith(".init"),
        "table_name": TABLE_NAME,
    }})

    try:
        payload = _parse_payload(event)
        logger.debug("request_payload", extra={"extra": {
            "request_id": request_id,
            # Avoid logging secrets/PII beyond what’s necessary
            "fields_present": list(payload.keys()) if isinstance(payload, dict) else [],
        }})

    except Exception as e:
        logger.warning("invalid_json", extra={"extra": {
            "request_id": request_id, "error": str(e)
        }})
        return _response(400, {"error": f"Invalid JSON body: {str(e)}"})

    # Validate required fields
    required = ["username", "first_name", "last_name"]
    missing = [f for f in required if f not in payload]
    if missing:
        logger.warning("missing_fields", extra={"extra": {
            "request_id": request_id, "missing": missing
        }})
        return _response(400, {"error": f"Missing required fields: {', '.join(missing)}"})

    item = {
        "id": str(uuid.uuid4()),
        "username": payload["username"],
        "first_name": payload["first_name"],
        "last_name": payload["last_name"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
        logger.info("ddb_put_success", extra={"extra": {
            "request_id": request_id, "pk": item["id"], "username": item["username"],
            "function_name": function_name,
        }})
    except Exception as e:
        logger.error("ddb_put_failed", extra={"extra": {
            "request_id": request_id, "error": str(e),
            "function_name": function_name,
        }})
        raise Exception("Failed to create user") from e
    latency_ms = int((time.time() - start) * 1000)
    logger.info("request_completed", extra={"extra": {
        "request_id": request_id, "status": 201, "latency_ms": latency_ms
    }})

    # Add request_id in response headers for traceability
    return _response(201, {"message": "User created", "user": item}, headers={"x-request-id": request_id})
