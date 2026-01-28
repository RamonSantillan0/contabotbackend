from fastapi import APIRouter, Depends

from app.schemas.agent import AgentRequest, AgentResponse
from app.services.agent_logic import handle_agent
from app.db.repository import get_db

router = APIRouter()

@router.post("/agent", response_model=AgentResponse)
def agent(req: AgentRequest, db=Depends(get_db)):
    return handle_agent(db, req)
