from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from .adapters.mock_systems import MockSystems
from .clock import Clock, FixedClock, SystemClock
from .errors import BookingNotFound, ConfirmationRefused, ContractViolation, SourceUnavailable
from .service import DisruptionService

BookingId = Annotated[str, Path(pattern=r"^[A-Z0-9]{6}$", description="6-char booking reference")]


class AssessRequest(BaseModel):
    compensation_requested: bool = False
    dry_run: bool = False


class ConfirmRequest(BaseModel):
    option_id: str


def default_service() -> DisruptionService:
    """Demo wiring: mock systems. NUBARA_FIXED_NOW pins the clock so the 2026-09-15
    sample data stays 'fresh' (e.g. 2026-09-15T11:02:35Z)."""
    fixed = os.getenv("NUBARA_FIXED_NOW")
    clock: Clock = (FixedClock(datetime.fromisoformat(fixed.replace("Z", "+00:00")).astimezone(timezone.utc))
                    if fixed else SystemClock())
    return DisruptionService(MockSystems(clock), clock)


def create_app(service: DisruptionService | None = None) -> FastAPI:
    svc = service or default_service()
    app = FastAPI(title="Nubara Disruption Companion", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/disruptions/{booking_id}/assess")
    def assess(booking_id: BookingId, body: AssessRequest | None = None) -> dict:
        body = body or AssessRequest()
        try:
            return svc.assess(booking_id, compensation_requested=body.compensation_requested,
                              dry_run=body.dry_run)
        except BookingNotFound:
            raise HTTPException(404, "booking_not_found") from None
        except (SourceUnavailable, ContractViolation):
            raise HTTPException(502, "upstream_unavailable") from None

    @app.post("/v1/disruptions/{booking_id}/confirm")
    def confirm(booking_id: BookingId, body: ConfirmRequest) -> dict:
        try:
            return svc.confirm_rebooking(booking_id, body.option_id)
        except BookingNotFound:
            raise HTTPException(404, "booking_not_found") from None
        except ConfirmationRefused as exc:
            raise HTTPException(409, str(exc)) from None
        except (SourceUnavailable, ContractViolation):
            raise HTTPException(502, "upstream_unavailable") from None

    return app


app = create_app()
