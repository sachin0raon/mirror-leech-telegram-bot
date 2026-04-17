from time import time

from .... import LOGGER, task_dict, task_dict_lock, external_aria2_downloads, external_listener_lock
from ....core.config_manager import Config
from ....core.torrent_manager import TorrentManager, aria2_name
from ...ext_utils.status_utils import (
    MirrorStatus,
    get_readable_file_size,
    get_readable_time,
)


class ExternalAria2Status:
    """
    Read-only status wrapper for aria2 downloads that were added externally
    (e.g. via the aria2 RPC interface directly, not via a bot command).

    Uses a self-referential ``listener`` so the existing
    ``get_readable_message()`` / ``cancel`` pipeline works without modification.
    """

    def __init__(self, gid: str, download: dict, task_key: str):
        self._gid = gid
        self._download = download
        self._task_key = task_key          # key used in task_dict, e.g. "exta2_<gid[:8]>"
        self.download_start_time = time()
        self.seeding = False
        self.queued = False
        self.start_time = 0
        self.tool = "aria2"

        # --- self-referential listener fields expected by the status pipeline ---
        self.listener = self
        self.user_id = Config.OWNER_ID     # only owner can cancel external tasks
        self.is_super_chat = False         # suppresses message.link path
        self.is_qbit = False
        self.is_torrent = True
        self.is_cancelled = False
        self.subname = ""
        self.progress = True
        self.subsize = 0
        self.files_to_proceed = []
        self.proceed_count = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _refresh(self):
        try:
            result = await TorrentManager.aria2.tellStatus(self._gid)
            if result:
                self._download = result
                # Handle magnet → torrent GID transition
                if self._download.get("followedBy", []):
                    new_gid = self._download["followedBy"][0]
                    if new_gid != self._gid:
                        # Update registry key
                        async with external_listener_lock:
                            if self._gid in external_aria2_downloads:
                                del external_aria2_downloads[self._gid]
                            external_aria2_downloads[new_gid] = self
                        # Update task_dict key
                        old_key = self._task_key
                        new_key = f"exta2_{new_gid[:8]}"
                        async with task_dict_lock:
                            if old_key in task_dict:
                                task_dict[new_key] = task_dict.pop(old_key)
                        self._gid = new_gid
                        self._task_key = new_key
                        self._download = await TorrentManager.aria2.tellStatus(self._gid)
        except Exception as e:
            LOGGER.error(f"ExternalAria2Status: failed to refresh {self._gid}: {e}")

    # ------------------------------------------------------------------
    # Status interface (mirrors Aria2Status)
    # ------------------------------------------------------------------

    async def update(self):
        await self._refresh()

    def progress(self):
        try:
            return f"{round(int(self._download.get('completedLength', '0')) / int(self._download.get('totalLength', '0')) * 100, 2)}%"
        except Exception:
            return "0%"

    def processed_bytes(self):
        return get_readable_file_size(int(self._download.get("completedLength", "0")))

    def speed(self):
        return f"{get_readable_file_size(int(self._download.get('downloadSpeed', '0')))}/s"

    def name(self):
        return aria2_name(self._download)

    def size(self):
        return get_readable_file_size(int(self._download.get("totalLength", "0")))

    def eta(self):
        try:
            return get_readable_time(
                int(
                    (int(self._download.get("totalLength", "0")) - int(self._download.get("completedLength", "0")))
                    / int(self._download.get("downloadSpeed", "0"))
                )
            )
        except Exception:
            return "-"

    async def status(self):
        await self._refresh()
        dl_status = self._download.get("status", "")
        if dl_status == "waiting" or self.queued:
            return MirrorStatus.STATUS_QUEUEDL
        elif dl_status == "paused":
            return MirrorStatus.STATUS_PAUSED
        elif self._download.get("seeder", "") == "true" and self.seeding:
            return MirrorStatus.STATUS_SEED
        else:
            return MirrorStatus.STATUS_DOWNLOAD

    def seeders_num(self):
        return self._download.get("numSeeders", 0)

    def leechers_num(self):
        return self._download.get("connections", 0)

    def uploaded_bytes(self):
        return get_readable_file_size(int(self._download.get("uploadLength", "0")))

    def seed_speed(self):
        return f"{get_readable_file_size(int(self._download.get('uploadSpeed', '0')))}/s"

    def ratio(self):
        try:
            return round(
                int(self._download.get("uploadLength", "0")) / int(self._download.get("completedLength", "0")),
                3,
            )
        except Exception:
            return 0

    def seeding_time(self):
        return get_readable_time(time() - self.start_time)

    def task(self):
        return self

    def gid(self):
        return self._gid

    # ------------------------------------------------------------------
    # Cancellation — removes the download from aria2, then cleans up
    # task_dict / external_aria2_downloads. No Telegram callbacks.
    # ------------------------------------------------------------------

    async def cancel_task(self):
        self.is_cancelled = True
        await self._refresh()
        name = self.name() or self._gid
        LOGGER.info(f"ExternalAria2Status: cancelling external download '{name}' ({self._gid})")
        try:
            await TorrentManager.aria2_remove(self._download)
        except Exception as e:
            LOGGER.error(f"ExternalAria2Status: error while removing {self._gid}: {e}")

        async with task_dict_lock:
            if self._task_key in task_dict:
                del task_dict[self._task_key]

        async with external_listener_lock:
            if self._gid in external_aria2_downloads:
                del external_aria2_downloads[self._gid]

        LOGGER.info(f"ExternalAria2Status: removed '{name}' from tracking")
