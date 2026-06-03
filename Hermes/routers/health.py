from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "hermes-gateway",
        "version": "0.1.0",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/")
async def root():
    return {"message": "Hermes Gateway running", "docs": "/docs"}
