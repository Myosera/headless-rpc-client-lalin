import logging
import multiprocessing
import sys
import threading
import time

from bot import run_bot
from rpc import update_presence
from discord_client import run_discord, wait_for_discord_ipc

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

shutdown_event = threading.Event()
active_processes = {}


def supervise_worker(name, target):
    retries = 0

    while not shutdown_event.is_set():
        proc = multiprocessing.Process(target=target, name=name)
        active_processes[name] = proc
        proc.start()
        proc.join()
        active_processes.pop(name, None)

        if proc.exitcode == 0:
            logging.info("%s exited cleanly.", name)
            return

        retries += 1
        if retries >= 5:
            logging.error(
                "%s reached the maximum retry limit (5). This worker is stopped, but the other one continues running.",
                name,
            )
            return

        logging.warning(
            "%s stopped unexpectedly. Retrying in 60 seconds (%d/5).",
            name,
            retries,
        )
        time.sleep(60)


def main():
    # 1. Jalankan Discord client lebih dulu
    discord_thread = threading.Thread(
        target=supervise_worker, args=("discord-client", run_discord), daemon=True
    )
    discord_thread.start()

    # 2. Tunggu sampai IPC socket Discord benar-benar muncul
    logging.info("Menunggu Discord IPC socket siap...")
    if not wait_for_discord_ipc(timeout=60, interval=1):
        logging.error("Discord IPC socket tidak muncul. Bot dan RPC tidak dijalankan.")
        return

    # 3. Baru jalankan bot & RPC setelah Discord siap
    threads = [
        threading.Thread(target=supervise_worker, args=("discord-bot", run_bot), daemon=True),
        threading.Thread(target=supervise_worker, args=("discord-rpc", update_presence), daemon=True),
    ]

    for thread in threads:
        thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_event.set()
        for proc in list(active_processes.values()):
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
        sys.exit(0)


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    main()