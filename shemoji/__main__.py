from __future__ import annotations

import asyncio

from .bot import run


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
