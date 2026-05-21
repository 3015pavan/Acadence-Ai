from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Body

from ..agent_schemas import AgentLogsResponse, AgentRunResponse, AgentStatusResponse
from ..agents.email_agent import email_agent
from ..auth import get_current_user, require_role


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/start", response_model=AgentStatusResponse)
def start_agent(user=Depends(require_role(["admin", "teacher"]))):
    return email_agent.start(owner_user_id=user.id, owner_role=user.role)


@router.post("/stop", response_model=AgentStatusResponse)
def stop_agent(user=Depends(require_role(["admin", "teacher"]))):
    return email_agent.stop(owner_user_id=user.id, owner_role=user.role)


@router.post("/run-now", response_model=AgentRunResponse)
def run_agent_now(user=Depends(require_role(["admin", "teacher", "operator"]))):
    try:
        email_agent._set_owner_context(user.id, user.role)
        return email_agent.run_once()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent run failed: {exc}") from exc


@router.get("/status", response_model=AgentStatusResponse)
def get_agent_status(user=Depends(get_current_user)):
    email_agent._set_owner_context(user.id, user.role)
    return email_agent.status()


@router.get("/logs", response_model=AgentLogsResponse)
def get_agent_logs(limit: int = Query(default=100, ge=1, le=500), user=Depends(require_role(["admin", "teacher", "operator"]))):
    return {"logs": email_agent.read_logs(limit=limit, owner_user_id=user.id)}


@router.get("/gmail/connect-url")
def gmail_connect_url(user=Depends(require_role(["admin", "teacher"]))):
    return email_agent.gmail_connect_url(owner_user_id=user.id, owner_role=user.role)


@router.get("/gmail/connect")
def gmail_connect(user=Depends(require_role(["admin", "teacher"]))):
    return RedirectResponse(url=email_agent.gmail_connect_url(owner_user_id=user.id, owner_role=user.role)["authorization_url"], status_code=302)


@router.get("/gmail/callback")
def gmail_callback(code: str = "", state: str = ""):
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    try:
        result = email_agent.gmail_complete_connection(code, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Gmail connection failed: {exc}") from exc

    return HTMLResponse(
        content=f"""
        <html>
          <body style=\"font-family: sans-serif; padding: 24px;\">
            <h2>Gmail connected</h2>
            <p>Connected as {result.get('connected_email') or 'the selected Google account'}.</p>
            <p>You can return to the app and start the agent automation.</p>
          </body>
        </html>
        """
    )


@router.post("/gmail/disconnect")
def gmail_disconnect(user=Depends(require_role(["admin", "teacher"]))):
    return email_agent.gmail_disconnect(owner_user_id=user.id, owner_role=user.role)
