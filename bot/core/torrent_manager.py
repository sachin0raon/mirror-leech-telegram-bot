from aioaria2 import Aria2WebsocketClient, exceptions
from aioqbt.client import create_client
from aioqbt.client import APIClient
from asyncio import gather, TimeoutError
from aiohttp import ClientError
from pathlib import Path
from inspect import iscoroutinefunction
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from urllib3.exceptions import HTTPError
from requests import exceptions as RequestsExceptions
from .. import LOGGER, aria2_options, is_empty_or_blank
from .config_manager import Config
from aiohttp.client_exceptions import ClientConnectionError

def wrap_with_retry(obj, max_retries=3):
    for attr_name in dir(obj):
        if attr_name.startswith("_"):
            continue

        attr = getattr(obj, attr_name)
        if iscoroutinefunction(attr):
            retry_policy = retry(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=5),
                retry=retry_if_exception_type(
                    (ClientError, TimeoutError, RuntimeError)
                ),
            )
            wrapped = retry_policy(attr)
            setattr(obj, attr_name, wrapped)
    return obj


class TorrentManager:
    aria2:Aria2WebsocketClient = None
    qbittorrent:APIClient = None

    @classmethod
    async def initiate(cls):
        try:
            aria_host = "http://localhost" if is_empty_or_blank(Config.ARIA_HOST) else Config.ARIA_HOST
            aria_port = 6800 if Config.ARIA_PORT is None else Config.ARIA_PORT
            aria_secret = "testing123" if is_empty_or_blank(Config.ARIA_SECRET) else Config.ARIA_SECRET
            qbit_host = "http://localhost:8090/api/v2/" if is_empty_or_blank(Config.QBIT_HOST) else f"{Config.QBIT_HOST}/api/v2/"
            qbit_user = None if is_empty_or_blank(Config.QBIT_USER) else Config.QBIT_USER
            qbit_pass = None if is_empty_or_blank(Config.QBIT_PASS) else Config.QBIT_PASS
            cls.aria2 = await Aria2WebsocketClient.new(url=f"{aria_host}:{aria_port}/jsonrpc", token=aria_secret)
            LOGGER.info("aria2c initialized")
            cls.qbittorrent = await create_client(url=qbit_host, username=qbit_user, password=qbit_pass)
            LOGGER.info("qBittorrent initialized")
        except (HTTPError, exceptions.Aria2rpcException, RequestsExceptions.RequestException,
                RequestsExceptions.ConnectionError, ClientConnectionError) as e:
            LOGGER.critical(f"Failed to initialize downloaders :: {str(e)}")

    @classmethod
    async def close_all(cls):
        await gather(cls.aria2.close(), cls.qbittorrent.close())

    @classmethod
    async def aria2_remove(cls, download):
        if download.get("status", "") in ["active", "paused", "waiting"]:
            await cls.aria2.forceRemove(download.get("gid", ""))
        else:
            try:
                await cls.aria2.removeDownloadResult(download.get("gid", ""))
            except:
                pass

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
        tasks = []
        tasks.extend(
            cls.aria2.forceRemove(download.get("gid")) for download in downloads
        )
        try:
            await gather(*tasks)
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
