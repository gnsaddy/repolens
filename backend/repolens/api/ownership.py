from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..services import ownership
from .repos import _get_repo

router = APIRouter(prefix="/api/ownership", tags=["ownership"])


@router.get("/overview")
def overview(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Who maintains which areas of a repository, and where knowledge is concentrated in one person."""
    return ownership.overview(db, _get_repo(db, p, repo_id))


@router.get("/path")
def path_owners(
    repo_id: str,
    path: str = Query(min_length=1, max_length=1000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    _get_repo(db, p, repo_id)
    return ownership.file_owners(db, repo_id, path)


@router.get("/experts")
def experts(
    repo_id: str,
    paths: str = Query(min_length=1, max_length=4000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """People to ask about a comma-separated list of files."""
    _get_repo(db, p, repo_id)
    return ownership.experts(db, repo_id, [x for x in paths.split(",") if x][:30])
