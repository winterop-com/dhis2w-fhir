"""How a refusal reaches the wire: the OperationOutcome response, and the handlers answering every failure with one."""

from __future__ import annotations

import logging

from dhis2w_fhir.r4 import OperationOutcome, OperationOutcomeIssue
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from dhis2w_fhir_serve.errors import (
    FHIR_JSON_MEDIA_TYPE,
    IssueCode,
    IssueSeverity,
    MethodNotAllowedError,
    NotAnEndpointError,
    ServeError,
)
from dhis2w_fhir_serve.log import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def outcome(
    status_code: int,
    severity: IssueSeverity,
    code: IssueCode,
    diagnostics: str,
    expression: tuple[str, ...] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the OperationOutcome response one failed interaction answers with."""
    body = OperationOutcome(
        issue=[
            OperationOutcomeIssue(
                severity=severity,
                code=code,
                diagnostics=diagnostics,
                expression=list(expression) if expression else None,
            )
        ]
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True, by_alias=True),
        media_type=FHIR_JSON_MEDIA_TYPE,
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Answer every failure - raised, routed, or unexpected - with an OperationOutcome."""
    app.add_exception_handler(ServeError, _handle_serve_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)


async def _handle_serve_error(request: Request, exception: Exception) -> Response:
    """Answer a raised `ServeError` with the status and issue code it names."""
    error = exception if isinstance(exception, ServeError) else ServeError(str(exception))
    return outcome(
        error.status_code, "error", error.issue_code, error.diagnostics, headers=error.response_headers() or None
    )


async def _handle_validation_error(request: Request, exception: Exception) -> Response:
    """Answer a request FastAPI could not read as invalid, naming the elements it stumbled on."""
    expression: tuple[str, ...] = ()
    if isinstance(exception, RequestValidationError):
        expression = tuple(".".join(str(part) for part in error["loc"]) for error in exception.errors())
    return outcome(400, "error", "invalid", "the request could not be read", expression or None)


async def _handle_http_exception(request: Request, exception: Exception) -> Response:
    """Answer Starlette's own routing failures - an unrouted path, a method the path does not take."""
    status_code = exception.status_code if isinstance(exception, StarletteHTTPException) else 500
    if status_code == 405:
        error = MethodNotAllowedError(request.method, request.url.path)
        return outcome(error.status_code, "error", error.issue_code, error.diagnostics)
    if status_code == 404:
        missing = NotAnEndpointError(request.url.path)
        return outcome(missing.status_code, "error", missing.issue_code, missing.diagnostics)
    detail = exception.detail if isinstance(exception, StarletteHTTPException) else "request failed"
    return outcome(status_code, "error", "processing", str(detail))


async def _handle_unexpected_error(request: Request, exception: Exception) -> Response:
    """Answer an unexpected failure with a bare outcome, and keep the traceback in the log."""
    logger.exception("%s %s failed unexpectedly", request.method, request.url.path)
    return outcome(500, "fatal", "exception", "the server failed to process this request")
