"""Project statistics (plan V2.0 sections 5.1, 9.7, 11.2).

One aggregate read behind the statistics screen. The list routes are paginated to a hard cap and
report no total, so adding up whatever a browser happened to receive would understate every number
on a project with more rows than one page. Only counts and sums leave this route: no row id, subject
or reason is part of the answer.
"""

from __future__ import annotations

from fastapi import APIRouter

from bioadapt_policies.access import Action

from .deps import SessionDep

router = APIRouter(tags=["statistics"])


@router.get("/projects/{project_id}/stats")
def project_stats(session: SessionDep) -> dict:
    """A project's own numbers, for the people who may read the project at all.

    The guard is ``PROJECT_READ`` rather than one per section: this answer carries no row content,
    and a role that may read a project may know how much that project holds. The sections that need
    a stronger action remain the routes that return the rows themselves.
    """
    session.enforce(Action.PROJECT_READ, session.project_id, "project")
    project = session.catalog.get_project()
    stats = session.catalog.project_stats(now=session.clock())
    return {
        "project_id": session.project_id,
        "project": {
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "thresholds_confirmed": project.thresholds.confirmed,
            "thresholds_version": project.thresholds.version,
            "review_load_budget_hours_per_week": project.review_load_budget_hours_per_week,
        },
        **stats,
    }
