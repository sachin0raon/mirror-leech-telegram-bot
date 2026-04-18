from aiofiles.os import remove, path as aiopath
from asyncio import sleep, TimeoutError
from time import time
from aiohttp.client_exceptions import ClientError

from ... import (
    task_dict_lock,
    task_dict,
    LOGGER,
    intervals,
    external_aria2_downloads,
    external_listener_lock,
)
from ...core.config_manager import Config
from ...core.torrent_manager import TorrentManager, is_metadata, aria2_name
from ..ext_utils.bot_utils import bt_selection_buttons
from ..ext_utils.files_utils import clean_unwanted
from ..ext_utils.status_utils import get_task_by_gid
from ..ext_utils.task_manager import stop_duplicate_check
from ..mirror_leech_utils.status_utils.aria2_status import Aria2Status
from ..mirror_leech_utils.status_utils.external_aria2_status import ExternalAria2Status
from ..telegram_helper.message_utils import (
    send_message,
    delete_message,
    update_status_message,
)


# ---------------------------------------------------------------------------
# External download helpers
# ---------------------------------------------------------------------------

async def _register_external_aria2(gid: str, download: dict):
    """Register an aria2 download not started by the bot into task_dict so it
    appears in /status output and can be cancelled via /cancel <gid>."""
    task_key = f"exta2_{gid[:8]}"

    # Fast check with sentinel to prevent double-registration across concurrent calls
    async with external_listener_lock:
        if gid in external_aria2_downloads:
            return   # already registered (or being registered)
        # Mark immediately so concurrent calls skip registration
        external_aria2_downloads[gid] = None

    name = aria2_name(download) or gid
    LOGGER.info(f"Detected external aria2 download: '{name}' (GID: {gid})")

    # Inject bot trackers if this is a BitTorrent download
    # API call is outside the lock to avoid blocking other coroutines
    if "bittorrent" in download and Config.BT_TRACKERS_ARIA:
        try:
            await TorrentManager.aria2.changeOption(
                gid, {"bt-tracker": Config.BT_TRACKERS_ARIA}
            )
            LOGGER.info(
                f"Added trackers to external aria2 download: '{name}'"
            )
        except Exception as e:
            LOGGER.warning(
                f"Failed to add trackers to external aria2 download '{name}': {e}"
            )

    status = ExternalAria2Status(gid, download, task_key)

    # Store real status object and inject into task_dict
    async with external_listener_lock:
        external_aria2_downloads[gid] = status
    async with task_dict_lock:
        task_dict[task_key] = status


async def _remove_external_aria2(gid: str):
    """Clean up after an externally tracked aria2 download finishes or errors."""
    async with external_listener_lock:
        status = external_aria2_downloads.pop(gid, None)
    if status is not None:
        async with task_dict_lock:
            if status._task_key in task_dict:
                del task_dict[status._task_key]
        LOGGER.info(f"Removed external aria2 download from tracking (GID: {gid})")


async def scan_existing_aria2_downloads():
    """Scan aria2 for downloads that were already active/waiting before the bot
    started and register them as external so they appear in /status.

    Called once at startup after callbacks are registered.
    """
    try:
        results = await TorrentManager.aria2.getGlobalStat()
        active_count = int(results.get("numActive", 0))
        waiting_count = int(results.get("numWaiting", 0))
        if active_count == 0 and waiting_count == 0:
            return

        downloads = []
        if active_count:
            active = await TorrentManager.aria2.tellActive()
            downloads.extend(active)
        if waiting_count:
            waiting = await TorrentManager.aria2.tellWaiting(0, waiting_count)
            downloads.extend(waiting)

        if not downloads:
            return

        # Collect full GIDs already managed by the bot to avoid double-tracking
        async with task_dict_lock:
            bot_gids = set()
            for tk in task_dict.values():
                if callable(getattr(tk, "gid", None)):
                    bot_gids.add(tk.gid())
                # Also store hash for qBit tasks where gid() returns hash[:12]
                if callable(getattr(tk, "hash", None)):
                    bot_gids.add(tk.hash())

        registered = 0
        for download in downloads:
            gid = download.get("gid", "")
            if not gid:
                continue
            # Skip bot-managed downloads
            if gid in bot_gids or gid[:12] in bot_gids:
                continue
            # Skip metadata entries — they resolve to a real GID via followedBy
            # and will be registered when onDownloadStarted fires for that GID.
            if is_metadata(download):
                continue
            # Skip already-registered external downloads
            async with external_listener_lock:
                if gid in external_aria2_downloads:
                    continue
            await _register_external_aria2(gid, download)
            registered += 1

        if registered:
            LOGGER.info(
                f"Startup scan: registered {registered} pre-existing external "
                f"aria2 download(s)"
            )
        else:
            LOGGER.info("Startup scan: no new external aria2 downloads found")

    except Exception as e:
        LOGGER.error(f"scan_existing_aria2_downloads: {e}")


async def _on_download_started(api, data):
    gid = data["params"][0]["gid"]
    download = await api.tellStatus(gid)
    options = await api.getOption(gid)
    if options.get("follow-torrent", "") == "false":
        return
    if is_metadata(download):
        LOGGER.info(f"onDownloadStarted: {gid} METADATA")
        await sleep(1)
        if task := await get_task_by_gid(gid):
            # Skip bot-managed handling if this is already an external task
            if not isinstance(task, ExternalAria2Status):
                task.listener.is_torrent = True
                if task.listener.select:
                    metamsg = "Downloading Metadata, wait then you can select files. Use torrent file to avoid this wait."
                    meta = await send_message(task.listener.message, metamsg)
                    while True:
                        await sleep(0.5)
                        if download.get("status", "") == "removed" or download.get(
                            "followedBy", []
                        ):
                            await delete_message(meta)
                            break
                        download = await api.tellStatus(gid)
        else:
            # External metadata download — register it
            await _register_external_aria2(gid, download)
        return
    else:
        LOGGER.info(f"onDownloadStarted: {aria2_name(download)} - Gid: {gid}")
        await sleep(1)

    await sleep(2)
    if task := await get_task_by_gid(gid):
        # If this is already an externally-tracked download (e.g. a scan-registered
        # paused download that was just resumed), skip bot-managed handling entirely.
        # Running stop_duplicate_check or setting task.listener.name would corrupt
        # the ExternalAria2Status object (name() method shadowed by instance attr).
        if isinstance(task, ExternalAria2Status):
            return
        download = await api.tellStatus(gid)
        if "bittorrent" in download:
            task.listener.is_torrent = True
        task.listener.name = aria2_name(download)
        msg, button = await stop_duplicate_check(task.listener)

        if msg:
            await TorrentManager.aria2_remove(download)
            await task.listener.on_download_error(msg, button)
    else:
        # External download (HTTP/FTP/magnet that already resolved) — register it
        download = await api.tellStatus(gid)
        await _register_external_aria2(gid, download)


async def _on_download_complete(api, data):
    try:
        gid = data["params"][0]["gid"]
        download = await api.tellStatus(gid)
        options = await api.getOption(gid)
    except (TimeoutError, ClientError, Exception) as e:
        LOGGER.error(f"onDownloadComplete: {e}")
        return
    if options.get("follow-torrent", "") == "false":
        return
    if download.get("followedBy", []):
        new_gid = download.get("followedBy", [])[0]
        LOGGER.info(f"Gid changed from {gid} to {new_gid}")
        if task := await get_task_by_gid(new_gid):
            task.listener.is_torrent = True
            if Config.BASE_URL and task.listener.select:
                if not task.queued:
                    await api.forcePause(new_gid)
                SBUTTONS = bt_selection_buttons(new_gid)
                msg = "Your download paused. Choose files then press Done Selecting button to start downloading."
                await send_message(task.listener.message, msg, SBUTTONS)
        else:
            # External magnet resolved — the GID transition is handled by
            # ExternalAria2Status._refresh() on next update(). Just register
            # the new torrent GID if not already tracked.
            async with external_listener_lock:
                is_tracked = gid in external_aria2_downloads
            if is_tracked:
                pass  # _refresh() inside the status object handles the key swap
            else:
                new_download = await api.tellStatus(new_gid)
                await _register_external_aria2(new_gid, new_download)
    elif "bittorrent" in download:
        if task := await get_task_by_gid(gid):
            task.listener.is_torrent = True
            if hasattr(task, "seeding") and task.seeding:
                LOGGER.info(
                    f"Cancelling Seed: {aria2_name(download)} onDownloadComplete"
                )
                #await TorrentManager.aria2_remove(download)
                await task.listener.on_upload_error(
                    f"Seeding stopped with Ratio: {task.ratio()} and Time: {task.seeding_time()}"
                )
        else:
            # External BT download seeding completed — just clean up tracking
            await _remove_external_aria2(gid)
    else:
        LOGGER.info(f"onDownloadComplete: {aria2_name(download)} - Gid: {gid}")
        if task := await get_task_by_gid(gid):
            await task.listener.on_download_complete()
            if intervals["stopAll"]:
                return
            #await TorrentManager.aria2_remove(download)
        else:
            # External HTTP/FTP download completed — clean up tracking
            await _remove_external_aria2(gid)


async def _on_bt_download_complete(api, data):
    gid = data["params"][0]["gid"]
    await sleep(1)
    download = await api.tellStatus(gid)
    LOGGER.info(f"onBtDownloadComplete: {aria2_name(download)} - Gid: {gid}")
    if task := await get_task_by_gid(gid):
        task.listener.is_torrent = True
        if task.listener.select:
            res = download.get("files", [])
            for file_o in res:
                f_path = file_o.get("path", "")
                if file_o.get("selected", "") != "true" and await aiopath.exists(
                    f_path
                ):
                    try:
                        await remove(f_path)
                    except:
                        pass
            await clean_unwanted(download.get("dir", ""))
        if task.listener.seed:
            try:
                await api.changeOption(gid, {"max-upload-limit": "0"})
            except (TimeoutError, ClientError, Exception) as e:
                LOGGER.error(
                    f"{e} You are not able to seed because you added global option seed-time=0 without adding specific seed_time for this torrent GID: {gid}"
                )
        else:
            try:
                await api.forcePause(gid)
            except (TimeoutError, ClientError, Exception) as e:
                LOGGER.error(f"onBtDownloadComplete: {e} GID: {gid}")
        await task.listener.on_download_complete()
        if intervals["stopAll"]:
            return
        download = await api.tellStatus(gid)
        if (
            task.listener.seed
            and download.get("status", "") == "complete"
            and await get_task_by_gid(gid)
        ):
            LOGGER.info(f"Cancelling Seed: {aria2_name(download)}")
            #await TorrentManager.aria2_remove(download)
            await task.listener.on_upload_error(
                f"Seeding stopped with Ratio: {task.ratio()} and Time: {task.seeding_time()}"
            )
        elif (
            task.listener.seed
            and download.get("status", "") == "complete"
            and not await get_task_by_gid(gid)
        ):
            pass
        elif task.listener.seed and not task.listener.is_cancelled:
            async with task_dict_lock:
                if task.listener.mid not in task_dict:
                    #await TorrentManager.aria2_remove(download)
                    return
                task_dict[task.listener.mid] = Aria2Status(task.listener, gid, True)
                task_dict[task.listener.mid].start_time = time()
            LOGGER.info(f"Seeding started: {aria2_name(download)} - Gid: {gid}")
            await update_status_message(task.listener.message.chat.id)
        #else:
        #    await TorrentManager.aria2_remove(download)
    else:
        # External BT download completed — remove from tracking
        await _remove_external_aria2(gid)


async def _on_download_stopped(_, data):
    gid = data["params"][0]["gid"]
    await sleep(4)
    if task := await get_task_by_gid(gid):
        await task.listener.on_download_error("Dead torrent!")
    else:
        # External download stopped — clean up tracking
        await _remove_external_aria2(gid)


async def _on_download_error(api, data):
    gid = data["params"][0]["gid"]
    await sleep(1)
    LOGGER.info(f"onDownloadError: {gid}")
    error = "None"
    try:
        download = await api.tellStatus(gid)
        options = await api.getOption(gid)
        error = download.get("errorMessage", "")
        LOGGER.info(f"Download Error: {error}")
        if options.get("follow-torrent", "") == "false":
            return
    except (TimeoutError, ClientError, Exception) as e:
        return
    if task := await get_task_by_gid(gid):
        await task.listener.on_download_error(error)
    else:
        # External download errored — clean up tracking
        LOGGER.warning(f"External aria2 download error (GID: {gid}): {error}")
        await _remove_external_aria2(gid)


def add_aria2_callbacks():
    TorrentManager.aria2.onBtDownloadComplete(_on_bt_download_complete)
    TorrentManager.aria2.onDownloadComplete(_on_download_complete)
    TorrentManager.aria2.onDownloadError(_on_download_error)
    TorrentManager.aria2.onDownloadStart(_on_download_started)
    TorrentManager.aria2.onDownloadStop(_on_download_stopped)
