from pypresence import Presence, ActivityType, StatusDisplayType
import time
import random
import sys
import logging
import os

log_dir = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(log_dir, 'discord_rpc.log')

# Setup logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_path, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

client_id = "791259766554755102"
RPC = None
UPDATE_INTERVAL = 1  # Interval untuk update status (dalam detik)
RECONNECT_DELAY = 5  # Delay sebelum coba reconnect (dalam detik)
MAX_RETRIES = 5  # Maksimal retry koneksi

presences = [
    {
        "details": "アディショナルメモリ",
        "state": "Mekakucity Reload",
        "large_image": "https://media1.tenor.com/m/lMpJNGtaYPkAAAAC/additional-memory-kagerou-project.gif",
        "large_text": "JIN",
        "buttons": [{"label": "Watch", "url": "https://music.youtube.com/watch?v=Uffa2HdMlgM&si=XAqVSayIPiraJOIR"}],
        "interval": 240 
    },
    {
        "details": "アンチノミー",
        "state": "NieR:Automata OST",
        "large_image": "https://media1.tenor.com/m/p0M_edTyI3QAAAAC/harareta.gif",
        "large_text": "amazarashi",
        "buttons": [{"label": "Watch", "url": "https://music.youtube.com/watch?v=vIXnzeYcJC0&si=VlgQ0bAsmlrfA72F"}],
        "interval": 301
    },
    {
        "details": "ブラックボックス",
        "state": "NieR:Automata OST",
        "large_image": "https://media1.tenor.com/m/roHAeKcsRvkAAAAC/2b-9s.gif",
        "large_text": "LiSA",
        "buttons": [{"label": "Watch", "url": "https://music.youtube.com/watch?v=mV_ToixCcoY&si=EDav9MGGm3kKlLmM"}],
        "interval": 256
    },
    {
        "details": "さよならエンドロール",
        "state": "Under Blue",
        "large_image": "https://media1.tenor.com/m/9IbEGP8qlzwAAAAC/sunao-fuchi-sayonara-end-roll.gif",
        "large_text": "Eve",
        "buttons": [{"label": "Watch", "url": "https://music.youtube.com/watch?v=VOChndxKi6U&si=_uzDJ8k6i6lkC9NE"}],
        "interval": 216
    },
    {
        "details": "Shelter",
        "state": "Shelter: The Complete Edition",
        "large_image": "https://media1.tenor.com/m/4ilPte-dlqYAAAAC/porter-robinson-anime.gif",
        "large_text": "Porter Robinson & Madeon",
        "buttons": [{"label": "Watch", "url": "https://music.youtube.com/watch?v=fzQ6gRAEoy0&si=fgF0OgJLlGs3dpx9"}],
        "interval": 366
    }
]


def connect_rpc():
    """
    Koneksi ke Discord RPC dengan retry logic.
    Return: True jika berhasil, False jika gagal.
    """
    global RPC
    
    for attempt in range(MAX_RETRIES):
        try:
            if RPC:
                try:
                    RPC.close()
                except:
                    pass
            
            RPC = Presence(client_id=client_id)
            RPC.connect()
            logger.info("✓ Berhasil terhubung ke Discord RPC")
            return True
            
        except Exception as e:
            logger.warning(f"Koneksi RPC gagal (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            RPC = None
            
            if attempt < MAX_RETRIES - 1:
                logger.info(f"Retry dalam {RECONNECT_DELAY} detik...")
                time.sleep(RECONNECT_DELAY)
            else:
                logger.error("Gagal koneksi setelah semua retry")
    
    return False


def update_presence():
    """Update Discord presence dengan rotasi konfigurasi dan reconnection logic."""
    global RPC
    
    # Koneksi awal
    if not connect_rpc():
        logger.error("Tidak bisa mulai tanpa koneksi awal. Exit...")
        sys.exit(1)
    
    while True:
        try:
            # Cek koneksi sebelum mulai cycle baru
            if not RPC:
                logger.warning("RPC terputus, mencoba reconnect...")
                if not connect_rpc():
                    logger.error("Reconnect gagal, tunggu sebelum retry...")
                    time.sleep(RECONNECT_DELAY)
                    continue
            
            # Acak urutan konfigurasi untuk satu siklus
            shuffled = presences[:]
            random.shuffle(shuffled)

            for config in shuffled:
                # Cek koneksi sebelum setiap config
                if not RPC:
                    logger.warning("RPC terputus di tengah cycle, reconnect...")
                    if not connect_rpc():
                        logger.error("Reconnect gagal, skip config ini")
                        continue
                
                config["start"] = time.time()
                interval = config["interval"]
                start_loop = time.time()
                
                logger.info(f"Now playing: {config['details']} - {config['state']} ({interval}s)")

                # Countdown loop - update status setiap UPDATE_INTERVAL detik
                while True:
                    elapsed = time.time() - start_loop
                    remaining = max(0, interval - int(elapsed))

                    try:
                        RPC.update(
                            activity_type=ActivityType.LISTENING,
                            status_display_type=StatusDisplayType.DETAILS,
                            details=config["details"],
                            state=f"Album: {config['state']}",
                            large_image=config["large_image"],
                            large_text=f"By: {config['large_text']}",
                            buttons=config["buttons"],
                            start=config["start"],
                            end=config["start"] + interval
                        )
                    except Exception as e:
                        logger.error(f"Error updating presence: {e}")
                        RPC = None  # Set ke None agar reconnect di iterasi berikutnya
                        break  # Keluar dari countdown loop

                    if remaining <= 0:
                        break  # selesai countdown, lanjut ke konfigurasi berikutnya

                    time.sleep(UPDATE_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Program dihentikan oleh user")
            if RPC:
                try:
                    RPC.close()
                except:
                    pass
            sys.exit(0)
            
        except Exception as e:
            logger.error(f"Error di main loop: {e}")
            RPC = None
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    try:
        update_presence()
    except KeyboardInterrupt:
        logger.info("Program dihentikan")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)

