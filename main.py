import asyncio
import logging

from sniper_bot.scanner import SniperScanner


def main() -> None:
    scanner = SniperScanner()
    try:
        asyncio.run(scanner.run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Scanner stopped by user.")


if __name__ == "__main__":
    main()
