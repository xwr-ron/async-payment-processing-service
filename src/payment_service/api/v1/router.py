from fastapi import APIRouter

from payment_service.api.v1.payments import router as payments_router

router = APIRouter(prefix="/api/v1")
router.include_router(payments_router)
