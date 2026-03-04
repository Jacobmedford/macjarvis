from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import EmailSummary, get_session

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/emails", response_class=HTMLResponse)
async def get_emails(request: Request, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(50))
    email_list = result.scalars().all()
    return templates.TemplateResponse("emails.html", {"request": request, "emails": email_list})
