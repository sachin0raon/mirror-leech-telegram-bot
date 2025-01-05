from ..core.config_manager import Config
from ..helper.telegram_helper.message_utils import send_message
from ..helper.ext_utils.bot_utils import is_empty_or_blank, new_task
from .. import LOGGER
from asyncio import sleep
from requests import get as rget, exceptions
from pyngrok import ngrok, conf


def get_host_ngrok_info() -> str:
    ngrok_api_url = []
    msg = ""
    if is_empty_or_blank(Config.NGROK_HOST_URL):
        return msg
    for host_url in Config.NGROK_HOST_URL.split():
        ngrok_api_url.append(f"{host_url}/api/tunnels")
    for url in ngrok_api_url:
        LOGGER.info(f"Fetching host ngrok tunnels info :: {url}")
        try:
            response = rget(url, headers={'Content-Type': 'application/json'})
            if response.ok:
                tunnels = response.json()["tunnels"]
                for tunnel in tunnels:
                    if "ssh" not in tunnel["name"].lower():
                        msg += f'\n⚡ <b>{tunnel["name"]}</b>: <a href="{tunnel["public_url"]}">Click Here</a>'
                    else:
                        msg += f'\n⚡ <b>{tunnel["name"]}</b>: <code>{tunnel["public_url"]}</code>'
            else:
                LOGGER.error(f"Unable to get response from :: {url}")
            response.close()
        except exceptions.RequestException as err:
            LOGGER.error(f"Failed to get ngrok info from :: {url} [{err.__class__.__name__}]")
    return msg


@new_task
async def ngrok_info(client, message) -> None:
    if is_empty_or_blank(Config.NGROK_AUTH_TOKEN):
        await send_message(message, "<code>NGROK_AUTH_TOKEN</code> <b>is missing !</b>")
        return
    LOGGER.info("Getting ngrok tunnel info")
    try:
        if tunnels := ngrok.get_tunnels():
            await send_message(message, f"🌐 <b>Bot file server</b>: <a href='{tunnels[0].public_url}'>Click Here</a>{get_host_ngrok_info()}")
        else:
            raise IndexError("No tunnel found")
    except (IndexError, ngrok.PyngrokNgrokURLError, ngrok.PyngrokNgrokHTTPError):
        LOGGER.warning(f"Failed to get ngrok tunnel, restarting")
        try:
            if ngrok.process.is_process_running(conf.get_default().ngrok_path) is True:
                ngrok.kill()
                await sleep(2)
            file_tunnel = ngrok.connect(addr=f"file://{Config.DOWNLOAD_DIR}", proto="http", schemes=["https"], name="files_tunnel", inspect=False)
            await send_message(message, f"🌍 <b>Ngrok tunnel started\n⚡ Bot file server</b>: <a href='{file_tunnel.public_url}'>Click Here</a>{get_host_ngrok_info()}")
        except ngrok.PyngrokError as err:
            LOGGER.error("Failed to start ngrok tunnel")
            await send_message(message, f"⁉️ <b>Failed to get tunnel info</b>\nError: <code>{str(err)}</code>")
