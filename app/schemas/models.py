from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class InboundMessage(BaseModel):
    """Normalized inbound message sent to Central API."""

    canal: Literal["whatsapp", "outlook"] = Field(..., description="Canal de origem da mensagem")
    cliente_nome: str = Field(..., min_length=1, max_length=255, description="Nome do cliente")
    cliente_identificador: str = Field(..., min_length=1, max_length=255, description="Telefone E.164 sem + ou email")
    empresa_dominio: str = Field(..., min_length=1, max_length=255, description="Dominio da empresa")
    mensagem_id_externo: str = Field(..., min_length=1, max_length=255, description="ID nativo da mensagem no canal")
    pergunta: str = Field(..., min_length=1, description="Texto da mensagem do cliente")

    @field_validator("cliente_identificador")
    @classmethod
    def validate_identificador(cls, v: str, info) -> str:
        # Basic validation - more specific validation per channel happens in integrations
        return v.strip()

    @field_validator("pergunta")
    @classmethod
    def validate_pergunta(cls, v: str) -> str:
        return v.strip()


class OutboundWhatsAppRequest(BaseModel):
    """Request to send WhatsApp message."""

    telefone: str = Field(..., description="Telefone E.164 sem + (ex: 5511999999999)")
    mensagem: str = Field(..., min_length=1, max_length=4096, description="Texto da mensagem")

    @field_validator("telefone")
    @classmethod
    def validate_telefone(cls, v: str) -> str:
        v = v.strip().replace("+", "").replace(" ", "").replace("-", "")
        if not v.isdigit():
            raise ValueError("Telefone deve conter apenas digitos")
        if len(v) < 10 or len(v) > 15:
            raise ValueError("Telefone deve ter entre 10 e 15 digitos")
        return v


class OutboundOutlookRequest(BaseModel):
    """Request to send Outlook reply."""

    message_id: str = Field(..., description="ID da mensagem original no Microsoft Graph")
    resposta: str = Field(..., min_length=1, description="Texto da resposta")
    reply_all: bool = Field(default=False, description="Se deve responder a todos os destinatarios")


class CentralAPIResponse(BaseModel):
    """Response from Central API."""

    success: bool
    message: str | None = None
    data: dict | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: Literal["healthy", "unhealthy"]
    timestamp: datetime
    version: str
    checks: dict
