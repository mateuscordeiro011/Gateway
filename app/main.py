import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.clients.central_client import CentralAPIClient
from app.core.config import get_settings
from app.core.idempotency import IdempotencyStore, get_idempotency_store
from app.core.logging_config import get_logger, setup_logging
from app.core.security import verify_api_key
from app.integrations.outlook import OutlookIntegration, get_outlook_integration
from app.integrations.whatsapp import WhatsAppIntegration, get_whatsapp_integration
from app.schemas.models import (
    CentralAPIResponse,
    HealthResponse,
    InboundMessage,
    OutboundOutlookRequest,
    OutboundWhatsAppRequest,
)
from app.web.routes import router as web_router

# Setup logging
setup_logging()
logger = get_logger(__name__)

# Global instances
central_client: CentralAPIClient | None = None
whatsapp_integration: WhatsAppIntegration | None = None
outlook_integration: OutlookIntegration | None = None
idempotency_store: IdempotencyStore | None = None
scheduler_task: asyncio.Task | None = None

# Templates
templates = Jinja2Templates(directory="app/web/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global central_client, whatsapp_integration, outlook_integration, idempotency_store, scheduler_task

    settings = get_settings()

    logger.info("Starting gateway-comunicacao", extra={"version": "0.1.0"})

    # Initialize idempotency store
    idempotency_store = await get_idempotency_store()
    logger.info("Idempotency store initialized", extra={"backend": settings.IDEMPOTENCY_BACKEND})

    # Initialize clients
    central_client = CentralAPIClient()
    whatsapp_integration = await get_whatsapp_integration(idempotency_store)
    outlook_integration = await get_outlook_integration(idempotency_store)

    logger.info("All integrations initialized")

    # Start subscription renewal scheduler
    scheduler_task = asyncio.create_task(subscription_renewal_scheduler())
    logger.info("Subscription renewal scheduler started")

    yield

    # Shutdown
    logger.info("Shutting down gateway-comunicacao")

    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

    if central_client:
        await central_client.close()
    if whatsapp_integration:
        await whatsapp_integration.close()
    if outlook_integration:
        await outlook_integration.close()
    if idempotency_store and hasattr(idempotency_store, "close"):
        await idempotency_store.close()

    logger.info("Shutdown complete")


app = FastAPI(
    title="Gateway de Comunicação",
    description="Adaptador WhatsApp/Outlook para sistema central de atendimento",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include web routes
app.include_router(web_router)


# ============================================================
# Web Interface
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the web interface."""
    settings = get_settings()

    whatsapp_status = "configured" if settings.WHATSAPP_ACCESS_TOKEN else "not_configured"
    outlook_status = "configured" if settings.GRAPH_CLIENT_SECRET else "not_configured"
    central_status = "configured" if settings.CENTRAL_API_URL else "not_configured"

    idempotency_stats = {"total": 0, "processed": 0, "pending": 0}
    if idempotency_store and hasattr(idempotency_store, "_store"):
        store = idempotency_store._store
        total = len(store)
        processed = sum(1 for v in store.values() if v.get("processed_at"))
        idempotency_stats = {"total": total, "processed": processed, "pending": total - processed}

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": {"username": "admin"},
        "settings": settings,
        "idempotency_stats": idempotency_stats,
        "whatsapp_status": whatsapp_status,
        "outlook_status": outlook_status,
        "central_status": central_status,
        "current_page": "dashboard",
    })


# ============================================================
# Health Check
# ============================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint - does not depend on external services."""

    checks = {
        "idempotency_store": "ok" if idempotency_store else "not_initialized",
        "central_api_client": "initialized" if central_client else "not_initialized",
        "whatsapp_integration": "initialized" if whatsapp_integration else "not_initialized",
        "outlook_integration": "initialized" if outlook_integration else "not_initialized",
    }

    # Determine overall status
    all_ok = all(v in ("ok", "initialized") for v in checks.values())

    return HealthResponse(
        status="healthy" if all_ok else "unhealthy",
        timestamp=datetime.utcnow(),
        version="0.1.0",
        checks=checks,
    )


# ============================================================
# WhatsApp Webhooks
# ============================================================

@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook_verification(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
):
    """Verify WhatsApp webhook subscription."""
    logger.info(
        "WhatsApp webhook verification request",
        extra={"hub_mode": hub_mode}
    )

    challenge = whatsapp_integration.verify_webhook(hub_mode, hub_challenge, hub_verify_token)

    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verify token"
        )

    return challenge


@app.post("/webhook/whatsapp")
async def whatsapp_webhook_receive(
    request: Request,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Receive WhatsApp webhook notifications."""
    # Get raw body for HMAC verification
    body = await request.body()

    logger.info(
        "WhatsApp webhook received",
        extra={"payload_size": len(body), "has_signature": x_hub_signature_256 is not None}
    )

    # Verify HMAC
    if not whatsapp_integration.verify_hmac(body, x_hub_signature_256 or ""):
        logger.warning("Invalid HMAC signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature"
        )

    # Parse JSON
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Invalid JSON payload", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON"
        ) from e

    # Parse messages
    try:
        messages = whatsapp_integration.parse_webhook_payload(payload)
    except Exception as e:
        logger.error("Error parsing webhook payload", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload structure"
        ) from e

    if not messages:
        logger.info("No valid messages in payload")
        return {"status": "ok", "processed": 0}

    # Process each message with idempotency
    processed = 0
    for msg_data in messages:
        message_id = f"whatsapp:{msg_data['mensagem_id_externo']}"

        # Try to acquire idempotency lock
        should_process = await idempotency_store.try_acquire(message_id)

        if not should_process:
            logger.info(
                "Duplicate message ignored",
                extra={"mensagem_id_externo": msg_data["mensagem_id_externo"]}
            )
            continue

        try:
            # Create normalized message
            message = InboundMessage(**msg_data)

            # Send to Central API
            await central_client.send_inbound_message(message)

            # Mark as processed
            await idempotency_store.mark_processed(message_id)
            processed += 1

            logger.info(
                "Message forwarded to Central API",
                extra={"mensagem_id_externo": message.mensagem_id_externo}
            )

        except Exception as e:
            # Release lock to allow retry
            await idempotency_store.release(message_id)
            logger.error(
                "Failed to process message",
                extra={"mensagem_id_externo": msg_data.get("mensagem_id_externo"), "error": str(e)}
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process message"
            ) from e

    return {"status": "ok", "processed": processed}


# ============================================================
# Outlook Webhooks
# ============================================================

@app.get("/webhook/outlook")
async def outlook_webhook_validation(validationToken: str = Query(None)):
    """Handle Graph webhook validation."""
    if validationToken:
        logger.info("Graph webhook validation request")
        token = outlook_integration.handle_validation_token(validationToken)
        return PlainTextResponse(content=token, status_code=200)

    # If no validationToken, it might be a notification (but GET shouldn't have notifications)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing validationToken"
    )


@app.post("/webhook/outlook")
async def outlook_webhook_receive(
    request: Request,
    clientState: str = Query(None),
):
    """Receive Graph change notifications."""
    logger.info("Graph webhook notification received", extra={"has_client_state": clientState is not None})

    # Validate clientState
    if not clientState or not outlook_integration.validate_client_state(clientState):
        logger.warning("Invalid clientState")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid clientState"
        )

    # Parse JSON
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Invalid JSON payload", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON"
        ) from e

    # Process notifications
    notifications = payload.get("value", [])
    if not notifications:
        logger.info("No notifications in payload")
        return {"status": "ok", "processed": 0}

    processed = 0
    for notification in notifications:
        try:
            messages = await outlook_integration.process_notification(notification)

            for msg_data in messages:
                message_id = f"outlook:{msg_data['mensagem_id_externo']}"

                # Try to acquire idempotency lock
                should_process = await idempotency_store.try_acquire(message_id)

                if not should_process:
                    logger.info(
                        "Duplicate Outlook message ignored",
                        extra={"mensagem_id_externo": msg_data["mensagem_id_externo"]}
                    )
                    continue

                try:
                    message = InboundMessage(**msg_data)
                    await central_client.send_inbound_message(message)
                    await idempotency_store.mark_processed(message_id)
                    processed += 1

                    logger.info(
                        "Outlook message forwarded to Central API",
                        extra={"mensagem_id_externo": message.mensagem_id_externo}
                    )

                except Exception as e:
                    await idempotency_store.release(message_id)
                    logger.error(
                        "Failed to process Outlook message",
                        extra={"mensagem_id_externo": msg_data.get("mensagem_id_externo"), "error": str(e)}
                    )
                    raise

        except Exception as e:
            logger.error("Error processing notification", extra={"error": str(e)})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process notification"
            ) from e

    return {"status": "ok", "processed": processed}


# ============================================================
# Outbound Endpoints (require API Key)
# ============================================================

@app.post("/api/v1/enviar/whatsapp", response_model=CentralAPIResponse)
async def send_whatsapp(
    request: OutboundWhatsAppRequest,
    api_key: str = Depends(verify_api_key),
):
    """Send a WhatsApp message (triggered by Central API)."""
    logger.info(
        "Outbound WhatsApp request",
        extra={"telefone": request.telefone, "message_length": len(request.mensagem)}
    )

    try:
        result = await whatsapp_integration.send_message(request)
        return CentralAPIResponse(
            success=True,
            message="Message sent successfully",
            data=result,
        )
    except httpx.HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            logger.error(
                "WhatsApp API client error",
                extra={"status_code": e.response.status_code, "response": e.response.text}
            )
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"WhatsApp API error: {e.response.text}"
            ) from e
        raise
    except Exception as e:
        logger.error("Failed to send WhatsApp message", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send message"
        ) from e


@app.post("/api/v1/enviar/outlook", response_model=CentralAPIResponse)
async def send_outlook(
    request: OutboundOutlookRequest,
    api_key: str = Depends(verify_api_key),
):
    """Send an Outlook reply (triggered by Central API)."""
    logger.info(
        "Outbound Outlook request",
        extra={"message_id": request.message_id, "reply_all": request.reply_all}
    )

    try:
        result = await outlook_integration.send_reply(request)
        return CentralAPIResponse(
            success=True,
            message="Reply sent successfully",
            data=result,
        )
    except httpx.HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            logger.error(
                "Graph API client error",
                extra={"status_code": e.response.status_code, "response": e.response.text}
            )
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Graph API error: {e.response.text}"
            ) from e
        raise
    except Exception as e:
        logger.error("Failed to send Outlook reply", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send reply"
        ) from e


# ============================================================
# Subscription Management (internal/admin)
# ============================================================

class SubscriptionRequest(BaseModel):
    notification_url: str


@app.post("/admin/subscriptions/outlook/create")
async def create_outlook_subscription(
    request: SubscriptionRequest,
    api_key: str = Depends(verify_api_key),
):
    """Create a new Outlook Graph subscription."""
    try:
        result = await outlook_integration.create_subscription(request.notification_url)
        return {"status": "created", "subscription": result}
    except Exception as e:
        logger.error("Failed to create subscription", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@app.post("/admin/subscriptions/outlook/renew/{subscription_id}")
async def renew_outlook_subscription(
    subscription_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Renew an Outlook Graph subscription."""
    try:
        result = await outlook_integration.renew_subscription(subscription_id)
        return {"status": "renewed", "subscription": result}
    except Exception as e:
        logger.error("Failed to renew subscription", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


@app.delete("/admin/subscriptions/outlook/{subscription_id}")
async def delete_outlook_subscription(
    subscription_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Delete an Outlook Graph subscription."""
    try:
        await outlook_integration.delete_subscription(subscription_id)
        return {"status": "deleted"}
    except Exception as e:
        logger.error("Failed to delete subscription", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e


# ============================================================
# Background Tasks
# ============================================================

async def subscription_renewal_scheduler():
    """Background task to renew Graph subscriptions before they expire."""
    settings = get_settings()
    check_interval = 3600  # Check every hour

    while True:
        try:
            await asyncio.sleep(check_interval)

            # This would need to track active subscriptions
            # For now, just log that scheduler is running
            logger.debug("Subscription renewal check")

            # Cleanup expired idempotency keys
            if idempotency_store:
                removed = await idempotency_store.cleanup_expired(settings.IDEMPOTENCY_TTL_SECONDS)
                if removed > 0:
                    logger.info("Cleaned up expired idempotency keys", extra={"count": removed})

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in subscription renewal scheduler", extra={"error": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
