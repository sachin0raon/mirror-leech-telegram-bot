from aioaria2 import Aria2WebsocketClient, exceptions
from aioqbt.client import create_client
from aioqbt.client import APIClient
from asyncio import gather
from pathlib import Path
from urllib3.exceptions import HTTPError
from requests import exceptions as RequestsExceptions
from .. import LOGGER, aria2_options, is_empty_or_blank
from .config_manager import Config

class TorrentManager:
    aria2:Aria2WebsocketClient = None
    qbittorrent:APIClient = None

    @classmethod
    async def initiate(cls):
        try:
            aria_host = "http://localhost" if is_empty_or_blank(Config.ARIA_HOST) else Config.ARIA_HOST
            aria_port = 6800 if Config.ARIA_PORT is None else Config.ARIA_PORT
            aria_secret = "testing123" if is_empty_or_blank(Config.ARIA_SECRET) else Config.ARIA_SECRET
            cls.aria2 = await Aria2WebsocketClient.new(url=f"{aria_host}:{aria_port}/jsonrpc", token=aria_secret)
            cls.qbittorrent = await create_client("http://localhost:8090/api/v2/")
        except (HTTPError, exceptions.Aria2rpcException, RequestsExceptions.RequestException, RequestsExceptions.ConnectionError) as e:
            LOGGER.critical(f"Failed to initialize aria2c :: {str(e)}")

    @classmethod
    async def close_all(cls):
        await cls.aria2.close()
        await cls.qbittorrent.close()

    @classmethod
    async def remove_all(cls):
        await cls.pause_all()
        await gather(
            cls.qbittorrent.torrents.delete("all", True),
            cls.aria2.purgeDownloadResult(),
        )
        downloads = []
        results = await gather(cls.aria2.tellActive(), cls.aria2.tellWaiting(0, 1000))
        for res in results:
            downloads.extend(res)
        for download in downloads:
            try:
                await cls.aria2.forceRemove(download.get("gid"))
            except:
                pass

    @classmethod
    async def overall_speed(cls):
        s1, s2 = await gather(
            cls.qbittorrent.transfer.info(), cls.aria2.getGlobalStat()
        )
        download_speed = s1.dl_info_speed + int(s2.get("downloadSpeed", "0"))
        upload_speed = s1.up_info_speed + int(s2.get("uploadSpeed", "0"))
        return download_speed, upload_speed

    @classmethod
    async def pause_all(cls):
        await gather(cls.aria2.forcePauseAll(), cls.qbittorrent.torrents.stop("all"))

    @classmethod
    async def change_aria2_option(cls, key, value):
        downloads = []
        results = await gather(cls.aria2.tellActive(), cls.aria2.tellWaiting(0, 1000))
        for res in results:
            downloads.extend(res)
            tasks = []
        for download in downloads:
            if download.get("status", "") != "complete":
                tasks.append(cls.aria2.changeOption(download.get("gid"), {key: value}))
        if tasks:
            try:
                await gather(*tasks)
            except Exception as e:
                LOGGER.error(e)
        if key not in ["checksum", "index-out", "out", "pause", "select-file"]:
            await cls.aria2.changeGlobalOption({key: value})
            aria2_options[key] = value


def aria2_name(download_info):
    if "bittorrent" in download_info and download_info["bittorrent"].get("info"):
        return download_info["bittorrent"]["info"]["name"]
    elif download_info.get("files"):
        if download_info["files"][0]["path"].startswith("[METADATA]"):
            return download_info["files"][0]["path"]
        file_path = download_info["files"][0]["path"]
        dir_path = download_info["dir"]
        if file_path.startswith(dir_path):
            return Path(file_path[len(dir_path) + 1 :]).parts[0]
        else:
            return ""
    else:
        return ""


def is_metadata(download_info):
    return any(
        f["path"].startswith("[METADATA]") for f in download_info.get("files", [])
    )
