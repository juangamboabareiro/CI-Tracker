"""Objetivos definidos por el usuario (v0.5)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..goals import goal_progress
from ..models import Goal
from ..schemas import GoalIn, GoalOut, GoalPeriod, GoalUpdate
from ..stats import compute_daily_totals
from ..timeutils import local_today
from .deps import find_language, get_db, get_settings_dep, stats_config

router = APIRouter(prefix="/goals")


def _goals_out(db: Session, settings: Settings, goals: list[Goal]) -> list[GoalOut]:
    totals = compute_daily_totals(db, stats_config(settings))
    today = local_today(settings.tz)
    return [goal_progress(goal, totals, today) for goal in goals]


@router.get("", response_model=list[GoalOut])
def list_goals(db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> list[GoalOut]:
    goals = db.scalars(select(Goal).options(selectinload(Goal.language)).order_by(Goal.period, Goal.id)).all()
    return _goals_out(db, settings, list(goals))


@router.post("", response_model=GoalOut, status_code=201)
def create_goal(
    payload: GoalIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> GoalOut:
    goal = Goal(
        language_id=find_language(db, payload.language).id if payload.language else None,
        period=payload.period.value,
        metric=payload.metric.value,
        target_seconds=payload.target_seconds,
        baseline_seconds=payload.baseline_seconds or None,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return _goals_out(db, settings, [goal])[0]


@router.patch("/{goal_id}", response_model=GoalOut)
def update_goal(
    goal_id: int, payload: GoalUpdate, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> GoalOut:
    """Ajusta la meta o las horas previas estimadas (sólo objetivos totales)."""
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("baseline_seconds") and goal.period != GoalPeriod.total.value:
        raise HTTPException(status_code=422, detail="baseline_seconds sólo aplica a objetivos totales")
    for key, value in changes.items():
        if value is not None:
            setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return _goals_out(db, settings, [goal])[0]


@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: int, db: Session = Depends(get_db)) -> Response:
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    db.delete(goal)
    db.commit()
    return Response(status_code=204)
