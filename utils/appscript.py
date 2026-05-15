import json
import os

import aiohttp

APPSCRIPT_URL: str = os.getenv("APPSCRIPT_URL", "")
SECRET_KEY: str = os.getenv("APPSCRIPT_SECRET", "")


async def call_api(action: str, **kwargs) -> dict:
    if not APPSCRIPT_URL:
        return {"success": False, "error": "APPSCRIPT_URL não configurada"}

    payload = {"action": action, "secret": SECRET_KEY, **kwargs}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                APPSCRIPT_URL,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return {"success": False, "error": f"HTTP {resp.status}"}
                return await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        return {"success": False, "error": f"Erro de conexão: {exc}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
