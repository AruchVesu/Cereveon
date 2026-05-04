from fastapi import APIRouter

router = APIRouter(prefix="/player", tags=["player"])


@router.get("/ping")
def player_ping():
    return {"player": "ok"}
