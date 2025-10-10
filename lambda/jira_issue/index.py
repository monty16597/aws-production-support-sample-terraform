"""AWS Lambda handler that raises Jira issues for CloudWatch alarms.

The function expects to be invoked either directly by a CloudWatch Alarm state
change event (EventBridge) or through an SNS notification that wraps the alarm
payload. The handler extracts the alarm metadata and uses the Jira REST API to
raise (or update) an issue in the configured project.

Required environment variables
-------------------------------
- ``JIRA_HOST``: Base URL of the Jira instance (e.g. https://<tenant>.atlassian.net)
- ``JIRA_EMAIL``: Username / email used for authenticating against Jira
- ``JIRA_API_TOKEN``: Jira API token for the above user
- ``JIRA_PROJECT_KEY`` or ``JIRA_PROJECT_NAME``: Project to create issues in

Optional environment variables
-------------------------------
- ``JIRA_ISSUE_TYPE`` (default: ``Task``)
- ``JIRA_DEFAULT_ASSIGNEE`` (deprecated, uses legacy username field)
- ``JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID`` (preferred for Jira Cloud)
- ``JIRA_ALARM_LABEL`` (default: ``cloudwatch-alarm``)
- ``JIRA_COMPONENTS`` (comma separated component names)
- ``LOG_LEVEL`` (default: ``INFO``)

The handler returns a JSON serialisable dict describing the outcome for every
alarm found in the payload, including the issue key or the error that occurred.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

import boto3
from jira import JIRA
from jira.exceptions import JIRAError

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - logging glue
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            payload.update(record.extra_data)
        return json.dumps(payload, default=str)


logger = logging.getLogger("jira-alarm")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
logger.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))
logger.propagate = False


# ---------------------------------------------------------------------------
# Environment helpers and Jira client bootstrap
# ---------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _fetch_secret_string(secret_id: str) -> str:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_id)
    if "SecretString" in response and response["SecretString"] is not None:
        return response["SecretString"]
    if "SecretBinary" in response and response["SecretBinary"] is not None:
        return base64.b64decode(response["SecretBinary"]).decode("utf-8")
    raise RuntimeError(f"Secret '{secret_id}' contains no retrievable value")


@lru_cache(maxsize=128)
def _resolve_secret_reference(value: str) -> str:
    if not value or not value.startswith("{{resolve:secretsmanager:") or not value.endswith("}}"):  # pragma: no cover - guard
        return value

    body = value[2:-2]  # trim leading {{ and trailing }}
    try:
        _, service, remainder = body.split(":", 2)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Malformed secrets manager reference: {value}") from exc

    if service != "secretsmanager":  # pragma: no cover - only handling secretsmanager
        return value

    if ":SecretString" not in remainder:
        raise ValueError(f"Secrets Manager reference missing SecretString directive: {value}")

    secret_id, json_selector = remainder.split(":SecretString", 1)
    secret_id = secret_id.strip()

    # json_selector may be like ':key::' or ':::' etc.
    key = None
    if json_selector.startswith(":"):
        json_selector = json_selector[1:]
        key, _, _ = json_selector.partition(":")
        key = key or None

    secret_string = _fetch_secret_string(secret_id)
    if not key:
        return secret_string

    try:
        secret_json = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Secret '{secret_id}' is not JSON; cannot extract key '{key}'") from exc

    if key not in secret_json:
        raise KeyError(f"Key '{key}' not found in secret '{secret_id}'")

    return secret_json[key]


@lru_cache(maxsize=1)
def _get_secret_payload() -> Dict[str, Any]:
    secret_name = os.getenv("JIRA_SECRET_NAME")
    if not secret_name:
        return {}

    secret_string = _fetch_secret_string(secret_name)
    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Secret '{secret_name}' is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Secret '{secret_name}' must resolve to a JSON object")

    return payload


def _get_env(name: str, *, required: bool = False, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if isinstance(value, str) and value:
        resolved = _resolve_secret_reference(value)
        value = resolved if resolved is not None else value

    if not value:
        secret_payload = _get_secret_payload()
        if name in secret_payload:
            value = secret_payload[name]

    if value is None:
        value = default

    if required and not value:
        raise EnvironmentError(f"Environment variable '{name}' is required")
    return value


@lru_cache(maxsize=1)
def get_jira_client() -> JIRA:
    host = _get_env("JIRA_HOST", required=True)
    email = _get_env("JIRA_EMAIL", required=True)
    token = _get_env("JIRA_API_TOKEN", required=True)
    logger.debug("initialising_jira_client", extra={"extra_data": {"host": host}})
    return JIRA(server=host, basic_auth=(email, token))


@lru_cache(maxsize=1)
def get_project_key() -> str:
    cached_key = _get_env("JIRA_PROJECT_KEY")
    if cached_key:
        return cached_key

    project_name = _get_env("JIRA_PROJECT_NAME", required=True)
    client = get_jira_client()
    logger.debug("resolving_project_key", extra={"extra_data": {"project_name": project_name}})
    for project in client.projects():
        if getattr(project, "name", "").lower() == project_name.lower():
            return project.key
    raise RuntimeError(f"Unable to resolve Jira project key for name '{project_name}'")


@lru_cache(maxsize=1)
def get_issue_type() -> str:
    return _get_env("JIRA_ISSUE_TYPE", default="Task") or "Task"


@lru_cache(maxsize=1)
def get_alarm_label() -> list[str]:
    # Jira labels must be lower-case letters, numbers, hyphen or underscore
    raw_label = _get_env("JIRA_ALARM_LABEL") or []
    if raw_label:
        raw_label = [part.strip().replace(" ", "-") for part in raw_label.split(",") if part.strip()]
    return raw_label


@lru_cache(maxsize=1)
def get_components() -> List[str]:
    raw = _get_env("JIRA_COMPONENTS")
    if not raw:
        return []
    return [component.strip() for component in raw.split(",") if component.strip()]


# ---------------------------------------------------------------------------
# Event parsing helpers
# ---------------------------------------------------------------------------


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _extract_alarm_payloads(event: Any) -> List[Dict[str, Any]]:
    if isinstance(event, str):
        event = _maybe_json(event)

    if isinstance(event, dict):
        # SNS fan-out structure
        if "Records" in event and isinstance(event["Records"], list):
            payloads: List[Dict[str, Any]] = []
            for record in event["Records"]:
                message = record.get("Sns", {}).get("Message") if isinstance(record, dict) else None
                if not message:
                    logger.warning("sns_record_missing_message", extra={"extra_data": {"record": record}})
                    continue
                candidate = _maybe_json(message)
                if isinstance(candidate, dict):
                    payloads.append(candidate)
                else:
                    payloads.append({"raw_message": message})
            return payloads

        # EventBridge delivery of alarm state changes
        if event.get("detail-type") == "CloudWatch Alarm State Change" and isinstance(event.get("detail"), dict):
            return [event["detail"]]

        # Direct invoke with the alarm body
        if {"AlarmName", "NewStateValue"}.issubset(event.keys()):
            return [event]

    if isinstance(event, list):
        payloads = []
        for item in event:
            candidate = _maybe_json(item)
            if isinstance(candidate, dict):
                payloads.append(candidate)
        return payloads

    raise ValueError("Unsupported event payload format for CloudWatch alarm")


# ---------------------------------------------------------------------------
# Jira issue creation helpers
# ---------------------------------------------------------------------------


def _build_summary(alarm: Dict[str, Any]) -> str:
    alarm_name = alarm.get("AlarmName", "Unknown Alarm")
    state = alarm.get("NewStateValue", "UNKNOWN")
    summary = f"[CloudWatch] {alarm_name} is {state}"
    return summary[:255]


def _build_description(alarm: Dict[str, Any]) -> str:
    parts = [
        "CloudWatch alarm transitioned state.",
        "",
        f"Alarm Name: {alarm.get('AlarmName', 'N/A')}",
        f"Alarm Description: {alarm.get('AlarmDescription', 'N/A')}",
        f"AWS Account: {alarm.get('AWSAccountId', 'N/A')}",
        f"Region: {alarm.get('Region', 'N/A')}",
        f"State Change Time: {alarm.get('StateChangeTime', 'N/A')}",
        f"Previous State: {alarm.get('OldStateValue', 'UNKNOWN')}",
        f"Current State: {alarm.get('NewStateValue', 'UNKNOWN')}",
        "",
        "New State Reason:",
        alarm.get("NewStateReason", "N/A"),
    ]

    trigger = alarm.get("Trigger")
    if trigger:
        parts.extend([
            "",
            "Trigger:",
            json.dumps(trigger, indent=2, default=str),
        ])

    if raw := alarm.get("raw_message"):
        parts.extend([
            "",
            "Original Message:",
            raw if isinstance(raw, str) else json.dumps(raw, indent=2, default=str),
        ])

    # parts.extend([
    #     "",
    #     "Alarm Payload:",
    #     json.dumps(alarm, indent=2, default=str),
    # ])
    return "\n".join(parts)


def _build_issue_fields(alarm: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {
        "project": {"key": get_project_key()},
        "summary": _build_summary(alarm),
        "description": _build_description(alarm),
        "issuetype": {"name": get_issue_type()},
        "labels": get_alarm_label(),
    }

    components = get_components()
    if components:
        fields["components"] = [{"name": name} for name in components]

    assignee_account_id = _get_env("JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID")
    if assignee_account_id:
        fields["assignee"] = {"id": assignee_account_id}
    else:
        assignee = _get_env("JIRA_DEFAULT_ASSIGNEE")
        if assignee:
            fields["assignee"] = {"name": assignee}

    return fields


def _update_existing_issue(client: JIRA, issue_key: str, alarm: Dict[str, Any]) -> Dict[str, Any]:
    comment_lines = [
        "CloudWatch alarm triggered again.",
        f"Current State: {alarm.get('NewStateValue', 'UNKNOWN')}",
        f"State Change Time: {alarm.get('StateChangeTime', 'N/A')}",
    ]
    reason = alarm.get("NewStateReason")
    if reason:
        comment_lines.extend(["", reason])
    comment_lines.extend([
        "",
        "Alarm Payload:",
        json.dumps(alarm, indent=2, default=str),
    ])

    client.add_comment(issue_key, "\n".join(comment_lines))
    return {"issue_key": issue_key, "action": "commented"}

# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------
def handler(event: Any, context: Any) -> Dict[str, Any]:
    request_id = getattr(context, "aws_request_id", None)
    logger.info("alarm_event_received", extra={"extra_data": {"request_id": request_id}})

    try:
        alarms = _extract_alarm_payloads(event)
        print("Parsed alarms:\n\n", alarms)
    except Exception as exc:
        logger.error("alarm_parse_failed", extra={"extra_data": {"error": str(exc)}})
        raise

    client = get_jira_client()
    outcomes: List[Dict[str, Any]] = []

    for alarm in alarms:
        try:
            if "raw_message" in alarm and len(alarm) == 1:
                # Nothing useful to create an issue; skip with a warning outcome.
                logger.warning("alarm_payload_unstructured", extra={"extra_data": {"request_id": request_id}})
                outcomes.append({"error": "unstructured_alarm_message", "alarm": alarm.get("raw_message")})
                continue

            fields = _build_issue_fields(alarm)
            print("Creating issue with fields:\n\n", fields)
            issue = client.create_issue(fields)
            outcome = {"issue_key": issue.key, "action": "created"}

            logger.info("jira_issue_processed", extra={"extra_data": outcome})
            outcomes.append(outcome)
        except JIRAError as jira_exc:
            logger.error(
                "jira_error",
                extra={"extra_data": {
                    "status_code": getattr(jira_exc, "status_code", None),
                    "text": getattr(jira_exc, "text", str(jira_exc)),
                }},
            )
            outcomes.append({
                "error": "jira_error",
                "details": getattr(jira_exc, "text", str(jira_exc)),
                "alarm": alarm.get("AlarmName"),
            })
        except Exception as exc:  # pragma: no cover - defensive catch-all
            logger.error(
                "jira_issue_unexpected_error",
                extra={"extra_data": {
                    "error": str(exc),
                    "trace": traceback.format_exc(),
                }},
            )
            outcomes.append({
                "error": "unexpected_exception",
                "details": str(exc),
                "alarm": alarm.get("AlarmName"),
            })

    return {
        "request_id": request_id,
        "processed": len(outcomes),
        "results": outcomes,
    }
