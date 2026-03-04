from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AgentEvent, Task, get_session

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/tasks", response_class=HTMLResponse)
async def get_tasks(request: Request, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Task).order_by(Task.created_at.desc()))
    task_list = result.scalars().all()
    return templates.TemplateResponse("tasks.html", {"request": request, "tasks": task_list})


@router.post("/tasks")
async def create_task(title: str = Form(...), session: AsyncSession = Depends(get_session)):
    task = Task(title=title, source="dashboard")
    session.add(task)
    event = AgentEvent(event_type="task_added", payload=f'{{"title": "{title}", "source": "dashboard"}}')
    session.add(event)
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.post("/tasks/{task_id}/done")
async def complete_task(task_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task:
        task.status = "done"
        await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)
