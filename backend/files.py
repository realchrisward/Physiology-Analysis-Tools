import os

from fastapi import APIRouter, Request

from backend.channel_selection import select_default_channel
from backend.extractors import extract_dataframe
from backend.models import FileImportResult, ImportRequest, ImportResponse

router = APIRouter(prefix="/files", tags=["files"])

TIME_COLUMN_NAMES = {"ts", "time"}
NEVER_A_CHANNEL = TIME_COLUMN_NAMES | {"comment"}


@router.post("/import", response_model=ImportResponse)
def import_files(payload: ImportRequest, request: Request) -> ImportResponse:
    registry: dict[str, FileImportResult] = request.app.state.imported_files
    results: list[FileImportResult] = []

    for path in payload.paths:
        filename = os.path.basename(path)
        try:
            df = extract_dataframe(path)
            channel_names = list(df.columns)
            time_column = next(
                (c for c in channel_names if c.lower() in TIME_COLUMN_NAMES), None
            )
            selection = select_default_channel(filename, channel_names)
            result = FileImportResult(
                path=path,
                filename=filename,
                status="ok",
                channels=[
                    c for c in channel_names if c.lower() not in NEVER_A_CHANNEL
                ],
                time_column=time_column,
                default_channel=selection.channel,
                default_channel_matched_rule=selection.matched_rule,
            )
        except Exception as e:
            result = FileImportResult(
                path=path, filename=filename, status="error", error=str(e)
            )

        registry[path] = result
        results.append(result)

    return ImportResponse(results=results)


@router.get("", response_model=list[FileImportResult])
def list_files(request: Request) -> list[FileImportResult]:
    return list(request.app.state.imported_files.values())
