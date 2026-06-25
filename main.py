"""Point d'entrée du Memecoin Sniper Bot."""
import asyncio, signal, sys
from orchestrator import Orchestrator
from utils.config_store import load_config
from utils.logger import get_logger
log = get_logger("main")

async def run():
    config=load_config(); log.info("config_loaded", extra={"dry_run":config["general"]["dry_run"]})
    orch=Orchestrator(config)
    loop=asyncio.get_running_loop(); stop=asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop.set)
        except NotImplementedError: pass
    await orch.start(); await stop.wait(); await orch.stop()

def main():
    try: asyncio.run(run())
    except KeyboardInterrupt: pass
    except Exception as e: log.exception("fatal_error", extra={"error":str(e)}); sys.exit(1)

if __name__ == "__main__": main()
