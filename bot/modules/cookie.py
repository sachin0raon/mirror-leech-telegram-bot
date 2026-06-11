from aiofiles import open as aiopen
from aiohttp import ClientSession

from .. import LOGGER
from ..core.config_manager import Config
from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message, edit_message

@new_task
async def update_cookie(_, message):
    args = message.text.split(maxsplit=1)
    url = args[1] if len(args) > 1 else Config.COOKIE_FILE_URL
    
    if not url or not url.strip():
        await send_message(message, "No URL provided and COOKIE_FILE_URL is empty.")
        return
        
    msg = await send_message(message, f"Downloading cookie file...")
    
    try:
        async with ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                if response.status == 200:
                    text = await response.text()
                    async with aiopen("/app/cookies.txt", "w", encoding="utf-8") as f:
                        await f.write(text)
                    await edit_message(msg, "Successfully updated cookies.txt")
                else:
                    await edit_message(msg, f"Failed to download cookie file. Status: {response.status}")
    except Exception as e:
        LOGGER.error(f"Failed to download cookie file: {e}")
        await edit_message(msg, f"Failed to download cookie file: {e}")
