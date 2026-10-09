import os

from fastapi import APIRouter, Request

from backend.channel_selection import NON_SIGNAL_CHANNELS, select_default_channel
from backend.extractors import extract_dataframe
from backend.locks import path_lock
from backend.models import (
    EvictRequest,
    EvictResult,
    FileImportResult,
    ImportRequest,
    ImportResponse,
)

router = APIRouter(prefix="/files", tags=["files"])

TIME_COLUMN_NAMES = NON_SIGNAL_CHANNELS - {"comment"}


def drop_path_keys(cache: dict, path: str) -> bool:
    """Remove every entry of a tuple-keyed cache whose first key element is
    `path`; True if anything was removed.

    Iterates a snapshot of the keys: other requests are inserting into these
    caches while this runs, and a live iteration would raise "dictionary
    changed size during iteration". `pop(..., None)` tolerates a concurrent
    removal of the same key.
    """
    removed = False
    for key in list(cache):
        if key[0] == path:
            cache.pop(key, None)
            removed = True
    return removed


@router.post("/import", response_model=ImportResponse)
def import_files(payload: ImportRequest, request: Request) -> ImportResponse:
    registry: dict[str, FileImportResult] = request.app.state.imported_files
    results: list[FileImportResult] = []

    for path in payload.paths:
        filename = os.path.basename(path)
        try:
            # Everything that can fail runs BEFORE any shared state is
            # touched, so a failure can never leave the caches half-updated
            # (new signal under an "error" registry entry).
            df = extract_dataframe(path)
            stat = os.stat(path)
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
            df = None

        with path_lock(path):
            if df is not None:
                request.app.state.signal_cache[path] = {
                    "df": df,
                    "time_column": time_column,
                }
                # A re-import means the on-disk file may have changed, so any
                # previously memoized /channels/window results for this path
                # are now stale and must be dropped rather than served on the
                # next matching query.
                for cache in (
                    request.app.state.window_cache,
                    request.app.state.filtered_signal_cache,
                ):
                    drop_path_keys(cache, path)
                registry[path] = result
            else:
                # A failed RE-import (the file vanished, is mid-write, ...)
                # must not take down a file that already imported fine: its
                # signal is still cached and every other endpoint keys off the
                # registry entry. The caller still gets this error below.
                existing = registry.get(path)
                if existing is None or existing.status != "ok":
                    registry[path] = result
        results.append(result)

    return ImportResponse(results=results)


@router.get("", response_model=list[FileImportResult])
def list_files(request: Request) -> list[FileImportResult]:
    return list(request.app.state.imported_files.values())


@router.post("/evict", response_model=EvictResult)
def evict_files(payload: EvictRequest, request: Request) -> EvictResult:
    """Drop every in-memory entry for the given recordings: the decoded signal,
    filtered copies, window results, beats, and the import record.

    Called when files leave the session so memory does not grow with every
    recording ever opened. SQLite is untouched (saved review work survives and
    is restored if the file is imported again). Paths with nothing cached are
    a no-op; `evicted` counts the paths that actually had something.
    """
    state = request.app.state
    evicted = 0
    try:
        for path in dict.fromkeys(payload.paths):
            # The path's lock, so this waits for an in-flight edit/arrhythmia
            # run on the file instead of pulling its frame out from under it.
            with path_lock(path):
                found = False
                for cache in (
                    state.signal_cache,
                    state.beat_cache,
                    state.typical_rr_cache,
                    state.imported_files,
                ):
                    if cache.pop(path, None) is not None:
                        found = True
                for cache in (state.window_cache, state.filtered_signal_cache):
                    if drop_path_keys(cache, path):
                        found = True
                if found:
                    evicted += 1
    except Exception as e:
        return EvictResult(status="error", evicted=evicted, error=str(e))
    return EvictResult(status="ok", evicted=evicted)
