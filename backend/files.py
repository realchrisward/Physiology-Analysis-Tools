import os

from fastapi import APIRouter, Request

from backend.channel_selection import NON_SIGNAL_CHANNELS, select_default_channel
from backend.extractors import extract_dataframe
from backend.models import FileImportResult, ImportRequest, ImportResponse

router = APIRouter(prefix="/files", tags=["files"])

TIME_COLUMN_NAMES = NON_SIGNAL_CHANNELS - {"comment"}


@router.post("/import", response_model=ImportResponse)
def import_files(payload: ImportRequest, request: Request) -> ImportResponse:
    registry: dict[str, FileImportResult] = request.app.state.imported_files
    results: list[FileImportResult] = []

    for path in payload.paths:
        filename = os.path.basename(path)
        try:
            df = extract_dataframe(path)
            stat = os.stat(path)
            channel_names = list(df.columns)
            time_column = next(
                (c for c in channel_names if c.lower() in TIME_COLUMN_NAMES), None
            )
            request.app.state.signal_cache[path] = {
                "df": df,
                "time_column": time_column,
            }
            # A re-import means the on-disk file may have changed, so any
            # previously memoized /channels/window results for this path are
            # now stale and must be dropped rather than served on the next
            # matching query.
            window_cache = request.app.state.window_cache
            stale_keys = [key for key in window_cache if key[0] == path]
            for key in stale_keys:
                del window_cache[key]
            selection = select_default_channel(filename, channel_names)
            result = FileImportResult(
                path=path,
                filename=filename,
                status="ok",
                channels=[
                    c for c in channel_names if c.lower() not in NON_SIGNAL_CHANNELS
                ],
                time_column=time_column,
                size=stat.st_size,
                modified_time=stat.st_mtime,
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
