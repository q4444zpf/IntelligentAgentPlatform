import logging

from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


class ControlError(ValueError):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class ControlForbiddenError(ControlError):
    def __init__(self, _detail: str = ""):
        super().__init__(403, "control_forbidden", "Control operation is not permitted")


class ControlNotFoundError(ControlForbiddenError):
    def __init__(self, _detail: str = ""):
        ControlError.__init__(self, 404, "control_not_found", "Control resource does not exist or is not visible")


class ControlValidationError(ControlError):
    def __init__(self, _detail: str = ""):
        super().__init__(422, "control_validation_failed", "Control request is invalid")


class ControlIdempotencyConflict(ControlError):
    def __init__(self, _detail: str = ""):
        super().__init__(409, "control_idempotency_conflict", "Idempotency key conflict")


class ControlCommandExpired(ControlValidationError):
    def __init__(self):
        ControlError.__init__(self, 410, "control_command_expired", "Control command has expired")


class ControlExecutionError(ControlError):
    def __init__(self, _detail: str = ""):
        super().__init__(409, "control_invalid_state", "Control command is in an invalid state")


class ControlExecutorUnavailable(ControlExecutionError):
    def __init__(self):
        ControlError.__init__(self, 503, "control_executor_unavailable", "Control executor is unavailable")


class ControlExecutionInProgress(ControlExecutionError):
    def __init__(self):
        ControlError.__init__(self, 409, "control_execution_in_progress", "Control execution is in progress")


async def control_error_handler(_request, error: ControlError):
    return JSONResponse(status_code=error.status_code, content={
        "code": error.code, "message": error.message, "details": error.details,
    })


async def unexpected_error_handler(_request, error: Exception):
    logger.error("Unhandled API error", exc_info=(type(error), error, error.__traceback__))
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
