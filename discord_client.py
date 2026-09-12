import glob
import logging
import os
import subprocess
import time


def run_discord():
    """
    Menjalankan Discord desktop client headless lewat xvfb-run.
    Fungsi ini blocking - akan terus berjalan selama proses Discord hidup,
    supaya cocok dipakai sebagai target multiprocessing di supervisor.
    """
    cmd = [
        "xvfb-run",
        "--auto-servernum",
        "--server-args=-screen 0 1280x1024x24",
        "discord",
        "--no-sandbox",
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        process.wait()  # blocking sampai Discord process berhenti/crash
    except Exception as e:
        logging.error("Gagal menjalankan Discord: %s", e)
        raise  # supaya exitcode != 0, biar supervisor retry


def find_discord_ipc_socket():
    """Cari IPC socket Discord di lokasi umum. Return path kalau ketemu, None kalau tidak."""
    candidates = glob.glob("/tmp/discord-ipc-*")
    candidates += glob.glob(f"/run/user/{os.getuid()}/discord-ipc-*")
    return candidates[0] if candidates else None


def wait_for_discord_ipc(timeout=60, interval=1):
    """
    Polling sampai IPC socket Discord muncul, atau timeout.
    Return True kalau socket ditemukan, False kalau timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        socket_path = find_discord_ipc_socket()
        if socket_path:
            logging.info("Discord IPC socket ditemukan: %s", socket_path)
            return True
        time.sleep(interval)

    logging.error("Timeout menunggu Discord IPC socket setelah %d detik.", timeout)
    return False