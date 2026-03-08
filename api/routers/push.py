"""Push notification subscription endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware import verify_api_key
from bot.config import settings
from bot.utils.push_notifications import add_subscription, remove_subscription

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/vapid-key")
async def get_vapid_key():
    """Return the VAPID public key for client-side subscription.

    No auth required — the public key is not a secret.
    """
    if not settings.vapid_public_key:
        raise HTTPException(status_code=404, detail="Push notifications not configured")
    return {"public_key": settings.vapid_public_key}


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict


@router.post("/subscribe")
async def subscribe(body: SubscribeRequest, _: str = Depends(verify_api_key)):
    """Register a push subscription."""
    sub = {
        "endpoint": body.endpoint,
        "keys": body.keys,
    }
    await add_subscription(sub)
    return {"status": "subscribed"}


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeRequest, _: str = Depends(verify_api_key)):
    """Remove a push subscription."""
    removed = await remove_subscription(body.endpoint)
    return {"status": "unsubscribed" if removed else "not_found"}
