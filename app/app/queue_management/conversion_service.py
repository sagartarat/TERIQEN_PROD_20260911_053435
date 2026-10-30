# app/queue_management/conversion_service.py

from typing import Any, Dict

from app.queue_management import operation_queue_service as oq_service


def precheck_queue_conversion(queue_id: int) -> Dict[str, Any]:
    return oq_service.precheck_queue_email_conversion(queue_id)


def build_conversion_plan_service(queue_id: int) -> Dict[str, Any]:
    """
    Safe wrapper:
    - Uses build_conversion_plan if present in operation_queue_service
    - Returns a minimal compatible response if not present
    """
    build_fn = getattr(oq_service, "build_conversion_plan", None)

    if callable(build_fn):
        return build_fn(queue_id)

    return {
        "success": True,
        "message": "Build conversion plan function is not implemented in operation_queue_service. Using runtime conversion flow directly.",
        "plan": {
            "queue_id": queue_id,
        },
    }


def convert_queue_to_business_records_service(
    queue_id: int,
    request_id: str,
    user_code: str,
) -> Dict[str, Any]:
    return oq_service.convert_queue_to_business_records(
        queue_id=queue_id,
        request_id=request_id,
        user_code=user_code,
    )


def send_with_conversion(
    queue_id: int,
    request_id: str,
    email_payload: Dict[str, Any],
    user_code: str,
) -> Dict[str, Any]:
    try:
        return oq_service.send_email_with_conversion(
            queue_id=queue_id,
            request_id=request_id,
            email_payload=email_payload,
            user_code=user_code,
        )

    except Exception as exc:
        return {
            "success": False,
            "status": "failed",
            "message": str(exc),
        }

def process_without_email_service(
    queue_id: int,
    request_id: str,
    user_code: str,
) -> Dict[str, Any]:
    try:
        return oq_service.process_without_email(
            queue_id=queue_id,
            request_id=request_id,
            user_code=user_code,
        )

    except Exception as exc:
        return {
            "success": False,
            "status": "failed",
            "message": str(exc),
        }
