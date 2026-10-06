from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.clients.central_client import CentralAPIClient
from app.core.config import get_settings
from app.core.idempotency import IdempotencyStore, get_idempotency_store
from app.integrations.outlook import OutlookIntegration, get_outlook_integration
from app.integrations.whatsapp import WhatsAppIntegration, get_whatsapp_integration
from app.schemas.models import InboundMessage, OutboundOutlookRequest, OutboundWhatsAppRequest

router = APIRouter(prefix="", tags=["web"])

# Templates
templates_dir = "app/web/templates"
templates = Jinja2Templates(directory=templates_dir)

# Global instances (will be initialized on startup)
web_whatsapp: WhatsAppIntegration | None = None
web_outlook: OutlookIntegration | None = None
web_central: CentralAPIClient | None = None
web_idempotency: IdempotencyStore | None = None


async def init_web_integrations():
    """Initialize integrations for web interface."""
    global web_whatsapp, web_outlook, web_central, web_idempotency

    web_idempotency = await get_idempotency_store()
    web_whatsapp = await get_whatsapp_integration(web_idempotency)
    web_outlook = await get_outlook_integration(web_idempotency)
    web_central = CentralAPIClient()


def get_current_user():
    """Placeholder for authentication - in production, implement proper auth."""
    return {"username": "admin"}


# ============================================================
# Dashboard
# ============================================================

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    """Main dashboard showing system status and recent activity."""
    settings = get_settings()

    # Get idempotency stats
    idempotency_stats = {}
    if web_idempotency and hasattr(web_idempotency, "_store"):
        store = web_idempotency._store
        total = len(store)
        processed = sum(1 for v in store.values() if v.get("processed_at"))
        pending = total - processed
        idempotency_stats = {"total": total, "processed": processed, "pending": pending}
    elif web_idempotency and hasattr(web_idempotency, "engine"):
        # For database backend, we'd query the DB
        idempotency_stats = {"total": 0, "processed": 0, "pending": 0}

    # Check integration status
    whatsapp_status = "configured" if settings.WHATSAPP_ACCESS_TOKEN else "not_configured"
    outlook_status = "configured" if settings.GRAPH_CLIENT_SECRET else "not_configured"
    central_status = "configured" if settings.CENTRAL_API_URL else "not_configured"

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "settings": settings,
        "idempotency_stats": idempotency_stats,
        "whatsapp_status": whatsapp_status,
        "outlook_status": outlook_status,
        "central_status": central_status,
        "current_page": "dashboard",
    })


# ============================================================
# Idempotency Management
# ============================================================

@router.get("/idempotency", response_class=HTMLResponse)
async def idempotency_page(request: Request, user: dict = Depends(get_current_user)):
    """View and manage idempotency keys."""

    keys = []
    if web_idempotency and hasattr(web_idempotency, "_store"):
        for key, value in web_idempotency._store.items():
            keys.append({
                "key": key,
                "created_at": datetime.fromtimestamp(value.get("created_at", 0)).isoformat() if value.get("created_at") else "N/A",
                "processed_at": datetime.fromtimestamp(value.get("processed_at", 0)).isoformat() if value.get("processed_at") else "Pending",
                "is_processed": value.get("processed_at") is not None,
            })

    return templates.TemplateResponse("idempotency.html", {
        "request": request,
        "user": user,
        "keys": keys,
        "current_page": "idempotency",
    })


@router.post("/idempotency/{key}/release", response_class=HTMLResponse)
async def release_idempotency_key(key: str, user: dict = Depends(get_current_user)):
    """Release an idempotency key to allow reprocessing."""
    if web_idempotency:
        await web_idempotency.release(key)
    return RedirectResponse(url="/idempotency", status_code=303)


@router.post("/idempotency/cleanup", response_class=HTMLResponse)
async def cleanup_expired_keys(ttl_hours: int = 24, user: dict = Depends(get_current_user)):
    """Clean up expired idempotency keys."""
    if web_idempotency:
        await web_idempotency.cleanup_expired(ttl_hours * 3600)
    return RedirectResponse(url="/idempotency", status_code=303)


# ============================================================
# WhatsApp Testing
# ============================================================

@router.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_page(request: Request, user: dict = Depends(get_current_user)):
    """WhatsApp testing interface."""
    settings = get_settings()
    return templates.TemplateResponse("whatsapp.html", {
        "request": request,
        "user": user,
        "phone_number_id": settings.WHATSAPP_PHONE_NUMBER_ID,
        "current_page": "whatsapp",
    })


@router.post("/whatsapp/test-send", response_class=HTMLResponse)
async def test_send_whatsapp(
    request: Request,
    telefone: str = Query(...),
    mensagem: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """Test sending a WhatsApp message."""
    if not web_whatsapp:
        return templates.TemplateResponse("whatsapp.html", {
            "request": request,
            "user": user,
            "error": "WhatsApp integration not initialized",
            "current_page": "whatsapp",
        })

    try:
        result = await web_whatsapp.send_message(OutboundWhatsAppRequest(
            telefone=telefone,
            mensagem=mensagem,
        ))
        return templates.TemplateResponse("whatsapp.html", {
            "request": request,
            "user": user,
            "success": f"Message sent! ID: {result.get('messages', [{}])[0].get('id', 'unknown')}",
            "current_page": "whatsapp",
        })
    except Exception as e:
        return templates.TemplateResponse("whatsapp.html", {
            "request": request,
            "user": user,
            "error": f"Failed to send: {str(e)}",
            "current_page": "whatsapp",
        })


# ============================================================
# Outlook Testing
# ============================================================

@router.get("/outlook", response_class=HTMLResponse)
async def outlook_page(request: Request, user: dict = Depends(get_current_user)):
    """Outlook testing interface."""
    return templates.TemplateResponse("outlook.html", {
        "request": request,
        "user": user,
        "current_page": "outlook",
    })


@router.post("/outlook/test-reply", response_class=HTMLResponse)
async def test_reply_outlook(
    request: Request,
    message_id: str = Query(...),
    resposta: str = Query(...),
    reply_all: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """Test replying to an Outlook message."""
    if not web_outlook:
        return templates.TemplateResponse("outlook.html", {
            "request": request,
            "user": user,
            "error": "Outlook integration not initialized",
            "current_page": "outlook",
        })

    try:
        result = await web_outlook.send_reply(OutboundOutlookRequest(
            message_id=message_id,
            resposta=resposta,
            reply_all=reply_all,
        ))
        return templates.TemplateResponse("outlook.html", {
            "request": request,
            "user": user,
            "success": f"Reply sent! ID: {result.get('id', 'unknown')}",
            "current_page": "outlook",
        })
    except Exception as e:
        return templates.TemplateResponse("outlook.html", {
            "request": request,
            "user": user,
            "error": f"Failed to send: {str(e)}",
            "current_page": "outlook",
        })


@router.post("/outlook/create-subscription", response_class=HTMLResponse)
async def create_outlook_subscription(
    request: Request,
    notification_url: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """Create a Graph subscription for Outlook notifications."""
    if not web_outlook:
        return templates.TemplateResponse("outlook.html", {
            "request": request,
            "user": user,
            "error": "Outlook integration not initialized",
            "current_page": "outlook",
        })

    try:
        result = await web_outlook.create_subscription(notification_url)
        return templates.TemplateResponse("outlook.html", {
            "request": request,
            "user": user,
            "success": f"Subscription created! ID: {result.get('id', 'unknown')}",
            "current_page": "outlook",
        })
    except Exception as e:
        return templates.TemplateResponse("outlook.html", {
            "request": request,
            "user": user,
            "error": f"Failed to create subscription: {str(e)}",
            "current_page": "outlook",
        })


# ============================================================
# Central API Testing
# ============================================================

@router.get("/central", response_class=HTMLResponse)
async def central_page(request: Request, user: dict = Depends(get_current_user)):
    """Central API testing interface."""
    return templates.TemplateResponse("central.html", {
        "request": request,
        "user": user,
        "current_page": "central",
    })


@router.post("/central/test-send", response_class=HTMLResponse)
async def test_send_central(
    request: Request,
    canal: str = Query(...),
    cliente_nome: str = Query(...),
    cliente_identificador: str = Query(...),
    empresa_dominio: str = Query(...),
    mensagem_id_externo: str = Query(...),
    pergunta: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """Test sending a message to Central API."""
    if not web_central:
        return templates.TemplateResponse("central.html", {
            "request": request,
            "user": user,
            "error": "Central API client not initialized",
            "current_page": "central",
        })

    try:
        message = InboundMessage(
            canal=canal,
            cliente_nome=cliente_nome,
            cliente_identificador=cliente_identificador,
            empresa_dominio=empresa_dominio,
            mensagem_id_externo=mensagem_id_externo,
            pergunta=pergunta,
        )
        result = await web_central.send_inbound_message(message)
        return templates.TemplateResponse("central.html", {
            "request": request,
            "user": user,
            "success": f"Message sent to Central API! Success: {result.success}, Data: {result.data}",
            "current_page": "central",
        })
    except Exception as e:
        return templates.TemplateResponse("central.html", {
            "request": request,
            "user": user,
            "error": f"Failed to send: {str(e)}",
            "current_page": "central",
        })


@router.get("/central/health", response_class=HTMLResponse)
async def check_central_health(request: Request, user: dict = Depends(get_current_user)):
    """Check Central API health."""
    if not web_central:
        return templates.TemplateResponse("central.html", {
            "request": request,
            "user": user,
            "error": "Central API client not initialized",
            "current_page": "central",
        })

    try:
        healthy = await web_central.health_check()
        return templates.TemplateResponse("central.html", {
            "request": request,
            "user": user,
            "success": f"Central API health: {'OK' if healthy else 'FAIL'}",
            "current_page": "central",
        })
    except Exception as e:
        return templates.TemplateResponse("central.html", {
            "request": request,
            "user": user,
            "error": f"Health check failed: {str(e)}",
            "current_page": "central",
        })


# ============================================================
# Configuration View
# ============================================================

@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, user: dict = Depends(get_current_user)):
    """View current configuration (with secrets masked)."""
    settings = get_settings()

    # Mask sensitive values
    config = {
        "CENTRAL_API_URL": settings.CENTRAL_API_URL,
        "GATEWAY_API_KEY": "***" if settings.GATEWAY_API_KEY else "",
        "WHATSAPP_VERIFY_TOKEN": "***" if settings.WHATSAPP_VERIFY_TOKEN else "",
        "WHATSAPP_APP_SECRET": "***" if settings.WHATSAPP_APP_SECRET else "",
        "WHATSAPP_ACCESS_TOKEN": "***" if settings.WHATSAPP_ACCESS_TOKEN else "",
        "WHATSAPP_PHONE_NUMBER_ID": settings.WHATSAPP_PHONE_NUMBER_ID or "",
        "WHATSAPP_BUSINESS_ACCOUNT_ID": settings.WHATSAPP_BUSINESS_ACCOUNT_ID or "",
        "WHATSAPP_COMPANY_MAPPING_FILE": settings.WHATSAPP_COMPANY_MAPPING_FILE,
        "GRAPH_TENANT_ID": settings.GRAPH_TENANT_ID or "",
        "GRAPH_CLIENT_ID": settings.GRAPH_CLIENT_ID or "",
        "GRAPH_CLIENT_SECRET": "***" if settings.GRAPH_CLIENT_SECRET else "",
        "GRAPH_NOTIFICATION_CLIENT_STATE": "***" if settings.GRAPH_NOTIFICATION_CLIENT_STATE else "",
        "GRAPH_SUBSCRIPTION_RESOURCE": settings.GRAPH_SUBSCRIPTION_RESOURCE,
        "REQUEST_TIMEOUT": settings.REQUEST_TIMEOUT,
        "RETRY_MAX_ATTEMPTS": settings.RETRY_MAX_ATTEMPTS,
        "RETRY_MIN_WAIT": settings.RETRY_MIN_WAIT,
        "RETRY_MAX_WAIT": settings.RETRY_MAX_WAIT,
        "IDEMPOTENCY_BACKEND": settings.IDEMPOTENCY_BACKEND,
        "IDEMPOTENCY_TTL_SECONDS": settings.IDEMPOTENCY_TTL_SECONDS,
        "LOG_LEVEL": settings.LOG_LEVEL,
    }

    return templates.TemplateResponse("config.html", {
        "request": request,
        "user": user,
        "config": config,
        "current_page": "config",
    })


# ============================================================
# Logs Viewer
# ============================================================

@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    level: str | None = Query(None),
    limit: int = Query(100),
    user: dict = Depends(get_current_user),
):
    """View application logs."""
    # In a real implementation, this would read from log files
    # For now, return empty logs with a message
    logs = [
        {"timestamp": datetime.now().isoformat(), "level": "INFO", "message": "Log viewer ready - configure file logging to see logs here"},
    ]

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "user": user,
        "logs": logs,
        "level_filter": level,
        "limit": limit,
        "current_page": "logs",
    })
