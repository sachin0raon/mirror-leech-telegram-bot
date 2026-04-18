from time import time

from .... import LOGGER, task_dict, task_dict_lock, external_qb_torrents, external_listener_lock
from ....core.config_manager import Config
from ....core.torrent_manager import TorrentManager
from ...ext_utils.status_utils import (
    MirrorStatus,
    get_readable_file_size,
    get_readable_time,
)


class ExternalQbitStatus:
    """
    Read-only status wrapper for qBittorrent torrents that were added directly
    through the qBittorrent Web UI (not via a bot command).

    Uses a self-referential ``listener`` so that the existing
    ``get_readable_message()`` / ``cancel`` pipeline works without modification.
    """

    def __init__(self, tor_info, task_key: str):
        self._info = tor_info
        self._task_key = task_key          # key used in task_dict, e.g. "extqb_<hash[:8]>"
        self.download_start_time = time()
        self.tool = "qbittorrent"

        # --- self-referential listener fields expected by the status pipeline ---
        self.listener = self
        self.user_id = Config.OWNER_ID     # only owner can cancel external tasks
        self.is_super_chat = False         # suppresses message.link path in get_readable_message
        self.is_qbit = True
        self.is_torrent = True
        self.is_cancelled = False
        self.seeding = False               # needed so get_task_by_gid calls update()
        self.subname = ""
        self.show_progress = True          # flag read as task.listener.progress in status rendering
        self.subsize = 0
        self.files_to_proceed = []
        self.proceed_count = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _refresh(self):
        try:
            result = await TorrentManager.qbittorrent.torrents.info(hashes=[self._info.hash])
            if result:
                self._info = result[0]
        except Exception as e:
            LOGGER.error(f"ExternalQbitStatus: failed to refresh {self._info.hash}: {e}")

    # ------------------------------------------------------------------
    # Status interface (mirrors QbittorrentStatus)
    # ------------------------------------------------------------------

    async def update(self):
        await self._refresh()

    def progress(self):
        try:
            return f"{round(self._info.progress * 100, 2)}%"
        except Exception:
            return "0%"

    def processed_bytes(self):
        return get_readable_file_size(self._info.downloaded)

    def speed(self):
        return f"{get_readable_file_size(self._info.dlspeed)}/s"

    def name(self):
        if self._info.state in ["metaDL", "checkingResumeData"]:
            return f"[METADATA]{self._info.name}"
        return self._info.name

    def size(self):
        return get_readable_file_size(self._info.size)

    def eta(self):
        try:
            return get_readable_time(int(self._info.eta.total_seconds()))
        except Exception:
            return "-"

    async def status(self):
        await self._refresh()
        state = self._info.state
        if state == "queuedDL":
            return MirrorStatus.STATUS_QUEUEDL
        elif state == "queuedUP":
            return MirrorStatus.STATUS_QUEUEUP
        elif state in ["stoppedDL", "stoppedUP"]:
            return MirrorStatus.STATUS_PAUSED
        elif state in ["checkingUP", "checkingDL"]:
            return MirrorStatus.STATUS_CHECK
        elif state in ["stalledUP", "uploading"]:
            return MirrorStatus.STATUS_SEED
        else:
            return MirrorStatus.STATUS_DOWNLOAD

    def seeders_num(self):
        return self._info.num_seeds

    def leechers_num(self):
        return self._info.num_leechs

    def uploaded_bytes(self):
        return get_readable_file_size(self._info.uploaded)

    def seed_speed(self):
        return f"{get_readable_file_size(self._info.upspeed)}/s"

    def ratio(self):
        return f"{round(self._info.ratio, 3)}"

    def seeding_time(self):
        try:
            return get_readable_time(int(self._info.seeding_time.total_seconds()))
        except Exception:
            return "-"

    def task(self):
        return self

    def gid(self):
        return self._info.hash[:12]

    def hash(self):
        return self._info.hash

    # ------------------------------------------------------------------
    # Cancellation — stops & removes the torrent from qBittorrent,
    # then cleans up task_dict / external_qb_torrents. No Telegram
    # callbacks because there is no originating message.
    # ------------------------------------------------------------------

    async def cancel_task(self):
        self.is_cancelled = True
        await self._refresh()
        hash_ = self._info.hash
        LOGGER.info(f"ExternalQbitStatus: cancelling external torrent {self._info.name} ({hash_})")
        try:
            await TorrentManager.qbittorrent.torrents.stop([hash_])
            await TorrentManager.qbittorrent.torrents.delete([hash_], True)
        except Exception as e:
            LOGGER.error(f"ExternalQbitStatus: error while deleting {hash_}: {e}")

        async with task_dict_lock:
            if self._task_key in task_dict:
                del task_dict[self._task_key]

        async with external_listener_lock:
            if hash_ in external_qb_torrents:
                del external_qb_torrents[hash_]

        LOGGER.info(f"ExternalQbitStatus: removed {self._info.name} from tracking")
