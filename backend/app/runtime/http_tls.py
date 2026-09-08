from pathlib import Path
import ssl

import anyio.to_thread
import certifi
import httpx


def _build_runtime_ssl_context() -> ssl.SSLContext:
    pem = Path(certifi.where()).read_text(encoding="ascii")
    return ssl.create_default_context(cadata=pem)


async def create_runtime_ssl_context() -> ssl.SSLContext:
    try:
        return await anyio.to_thread.run_sync(
            _build_runtime_ssl_context, abandon_on_cancel=True
        )
    except TimeoutError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise httpx.ConnectError(
            "Unable to initialize TLS verification"
        ) from error
