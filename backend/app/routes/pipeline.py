"""Starting pipeline runs and reading their history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PipelineRun
from app.pipeline.graph import pipeline_topology, run_pipeline
from app.schemas import PipelineRunOut

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/topology")
def topology() -> dict:
    """Node and edge definitions for the UI's pipeline diagram."""
    return pipeline_topology()


@router.post("/run", response_model=PipelineRunOut)
async def start_run(
    months: int = Query(12, ge=1, le=60),
    seed: int = Query(42, description="Fixes the synthetic dataset so runs are repeatable."),
    db: Session = Depends(get_db),
) -> PipelineRunOut:
    """Run the pipeline over the synthetic demo dataset.

    Real statements are ingested through `POST /ingest/file` or the CLI's
    `wimmg ingest`, both of which run this same graph in `files` mode.
    """
    run = await run_pipeline(db, mode="demo", seed=seed, months=months)
    return PipelineRunOut.model_validate(run)


@router.get("/runs", response_model=list[PipelineRunOut])
def list_runs(
    limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)
) -> list[PipelineRunOut]:
    runs = (
        db.query(PipelineRun).order_by(PipelineRun.id.desc()).limit(limit).all()
    )
    return [PipelineRunOut.model_validate(run) for run in runs]


@router.get("/runs/{run_id}", response_model=PipelineRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)) -> PipelineRunOut:
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(404, "No such run.")
    return PipelineRunOut.model_validate(run)
