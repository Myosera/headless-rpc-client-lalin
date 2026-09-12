import discord
import os
import asyncio
import time
import yt_dlp
import logging
import re
from dotenv import load_dotenv
from discord import Activity, ActivityType, Status
from yt_dlp.utils import sanitize_filename

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("discord_bot")
logger.setLevel(logging.ERROR)

URL_RE = re.compile(r'^(https?://|www\.)', re.I)

# Configuration constants
INACTIVITY_TIMEOUT = 15  # seconds before disconnecting inactive bot
PLAYLIST_ENTRY_LIMIT = 50
QUEUE_ITEM_KEY_SOURCE = "source"
QUEUE_ITEM_KEY_TITLE = "title"

def run_bot():
    load_dotenv()
    TOKEN = os.getenv('lalin')
    if not TOKEN:
        logger.error("Environment variable 'lalin' (TOKEN) not found.")
        return

    intents = discord.Intents.all()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        await client.change_presence(
            status=Status.online,
            activity=Activity(type=ActivityType.listening, name="|help")
        )
        logger.info(f'Logged in as {client.user}')
        client.loop.create_task(activity_watchdog())

    # per-guild state
    states = {}  # guild_id -> {"queue": [], "lock": asyncio.Lock(), "mode": "none", "now_playing": None, "vc": None}

    # yt-dlp options optimized for streaming (no extract/encode)
    yt_dl_options = {
        "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
        "default_search": "auto",
        "quiet": True,
        "no_warnings": True,
        "outtmpl": "%(title)s.%(ext)s",
        "prefer_ffmpeg": True,
        "noplaylist": False,
    }
    ytdl = yt_dlp.YoutubeDL(yt_dl_options)

    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn -filter:a "volume=0.25"'
    }

    def _ensure_state(guild_id):
        s = states.get(guild_id)
        if not s:
            s = {"queue": [], "lock": asyncio.Lock(), "mode": "none", "now_playing": None, "vc": None, "channel_id": None, "notify_channel_id": None, "inactive_since": None, "should_reconnect": False}
            states[guild_id] = s
        return s

    def _get_stream_url(info):
        if not info:
            return None
        if isinstance(info, str):
            return info
        url = info.get('url')
        if url:
            return url
        formats = info.get('formats') or []
        for f in formats:
            if isinstance(f, dict) and f.get('url'):
                return f.get('url')
        return info.get('webpage_url')

    def _create_player(stream_url):
        return discord.FFmpegOpusAudio(stream_url, **ffmpeg_options)

    async def _notify_channel(guild_id, content):
        state = states.get(guild_id)
        if not state:
            return
        channel_id = state.get("notify_channel_id")
        if not channel_id:
            return
        channel = client.get_channel(channel_id)
        if channel:
            try:
                await channel.send(content)
            except Exception:
                logger.exception("Failed to send notification message")

    async def activity_watchdog():
        try:
            while True:
                for guild_id, s in list(states.items()):
                    vc = s.get("vc")
                    channel = None
                    if s.get("channel_id"):
                        channel = client.get_channel(s.get("channel_id"))

                    # If voice client is disconnected but we should reconnect, try it.
                    if s.get("should_reconnect") and s.get("channel_id") and (not vc or not getattr(vc, "is_connected", lambda: False)()):
                        if channel:
                            try:
                                logger.info(f"Attempting to reconnect voice for guild {guild_id} to channel {channel.id}")
                                new_vc = await channel.connect()
                                s["vc"] = new_vc
                                vc = new_vc
                                if (s.get("now_playing") or s.get("queue")) and not vc.is_playing():
                                    client.loop.call_soon_threadsafe(play_next_song, guild_id)
                                await _notify_channel(guild_id, "✅ Bot berhasil reconnect setelah terputus dari internet.")
                            except Exception:
                                logger.exception("Reconnect to voice channel failed; will retry shortly")
                                continue

                    non_bot_members = []
                    if channel:
                        non_bot_members = [m for m in channel.members if not m.bot]

                    has_listeners = bool(non_bot_members)

                    if has_listeners:
                        s["inactive_since"] = None
                        continue

                    # No users in voice channel; start idle timer
                    if s.get("inactive_since") is None:
                        s["inactive_since"] = time.time()
                        continue

                    elapsed = time.time() - s.get("inactive_since", 0)
                    if elapsed >= INACTIVITY_TIMEOUT:
                        if vc:
                            try:
                                await vc.disconnect()
                            except Exception:
                                logger.exception("Error disconnecting inactive voice client")
                        logger.debug(f"Disconnected voice in guild {guild_id} due to {INACTIVITY_TIMEOUT}s no-users timeout")
                        await _notify_channel(guild_id, f"⏹️ Bot automatically disconnected due to no users in voice channel for {INACTIVITY_TIMEOUT} seconds.")
                        s["vc"] = None
                        s["channel_id"] = None
                        s["notify_channel_id"] = None
                        s["inactive_since"] = None
                        s["should_reconnect"] = False
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error in activity_watchdog")

    def _is_url(text):
        return bool(URL_RE.match(text))

    def _format_duration(seconds):
        """Format duration in seconds to MM:SS format."""
        if not isinstance(seconds, (int, float)):
            return "0:00"
        minutes = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{minutes}:{secs:02d}"

    def _create_metadata_embed(title, description, uploader, duration, views, thumbnail, is_playlist=False):
        """Create a discord embed for video/playlist metadata."""
        prefix = "Playlist Preview" if is_playlist else ""
        embed = discord.Embed(
            title=f"{prefix} 「** {title} **」".strip(),
            description=(description[:2048] if description else ""),
            color=discord.Color.blue()
        )
        embed.add_field(name="Channel", value=uploader or "Unknown", inline=True)
        embed.add_field(name="Duration", value=_format_duration(duration), inline=True)
        embed.add_field(name="Views", value=str(views or ""), inline=True)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        return embed

    def _extract_metadata_from_entry(entry):
        """Safely extract metadata from a yt-dlp entry dict."""
        if not isinstance(entry, dict):
            return None
        
        return {
            "title": entry.get("title") or "Unknown title",
            "description": entry.get("description") or "",
            "uploader": entry.get("uploader") or "Unknown",
            "duration": entry.get("duration") or 0,
            "views": entry.get("view_count") or "",
            "thumbnail": entry.get("thumbnail"),
            "url": _get_stream_url(entry)
        }

    def _is_url(text):
        return bool(URL_RE.match(text))

    async def _extract_info_safe(query):
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
            return data
        except yt_dlp.utils.DownloadError as de:
            logger.error(f"yt-dlp download error: {de}")
            return {"error": str(de)}
        except Exception as e:
            logger.exception("Unexpected error extracting info")
            return {"error": str(e)}

    def play_next_song(guild_id):
        try:
            s = states.get(guild_id)
            if not s:
                logger.info(f"No state for guild {guild_id}")
                return

            q = s["queue"]
            mode = s.get("mode", "none")
            vc = s.get("vc")

            if not q and mode != "one":
                # Keep the voice connection open for a short idle timeout instead of disconnecting immediately.
                s["now_playing"] = None
                logger.info(f"Queue empty for guild {guild_id}. Waiting for inactivity timeout before disconnect.")
                return

            if not vc or not getattr(vc, "is_connected", lambda: False)():
                logger.info(f"No voice client for guild {guild_id}. Clearing queue.")
                s["queue"] = []
                s["now_playing"] = None
                return

            if vc.is_playing():
                logger.debug("Voice client is already playing.")
                return

            if mode == "one" and s.get("now_playing"):
                item = s["now_playing"]
            else:
                if not q:
                    s["now_playing"] = None
                    if mode == "all" and q == []:
                        logger.info(f"Queue empty for guild {guild_id} with loop all.")
                        return
                    return
                item = q.pop(0)
                if mode == "all":
                    q.append(item)

            stream_url = item.get(QUEUE_ITEM_KEY_SOURCE)
            if not stream_url:
                logger.warning("Item has no source, skipping.")
                client.loop.call_soon_threadsafe(play_next_song, guild_id)
                return

            player = _create_player(stream_url)
            s["now_playing"] = item

            def _after(err):
                if err:
                    logger.error(f"Player error: {err}")
                try:
                    client.loop.call_soon_threadsafe(play_next_song, guild_id)
                except Exception as e:
                    logger.exception(f"Error scheduling next song: {e}")

            vc.play(player, after=_after)
            logger.info(f"Playing: {item.get(QUEUE_ITEM_KEY_TITLE,'Unknown')} in guild {guild_id}")
        except Exception:
            logger.exception("Error in play_next_song")

    @client.event
    async def on_message(message):
        if message.author.bot:
            return

        content = message.content.strip()
        if not content:
            return

        # HELP
        if content.startswith("|help"):
            try:
                embed = discord.Embed(
                    title="🎵 Bot Commands",
                    description="Available music bot commands:",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="|play <url or keyword>",
                    value="Play audio or add to queue",
                    inline=False
                )
                embed.add_field(
                    name="|skip",
                    value="Skip current song",
                    inline=False
                )
                embed.add_field(
                    name="|pause",
                    value="Pause playback",
                    inline=False
                )
                embed.add_field(
                    name="|resume",
                    value="Resume from pause",
                    inline=False
                )
                embed.add_field(
                    name="|stop",
                    value="Stop and reset queue",
                    inline=False
                )
                embed.add_field(
                    name="|loop <none|one|all>",
                    value="Set loop mode",
                    inline=False
                )
                embed.add_field(
                    name="|queue",
                    value="Show upcoming list (last 10 items)",
                    inline=False
                )
                embed.add_field(
                    name="|now",
                    value="Show currently playing song",
                    inline=False
                )
                await message.channel.send(embed=embed)
            except Exception:
                logger.exception("Error on help")
            return

        # PLAY
        if content.startswith("|play"):

            if not message.author.voice or not message.author.voice.channel:
                await message.channel.send("You must be connected to a voice channel to use |play.")
                return

            guild_id = message.guild.id
            state = _ensure_state(guild_id)

            try:
                vc = state.get("vc")
                if not vc or not getattr(vc, "is_connected", lambda: False)():
                    vc = await message.author.voice.channel.connect()
                    state["vc"] = vc
                state["channel_id"] = message.author.voice.channel.id
                state["notify_channel_id"] = message.channel.id
                state["should_reconnect"] = True
                state["inactive_since"] = None
            except Exception:
                logger.exception("Voice connect error")
                await message.channel.send("Failed to connect to voice channel.")
                return

            parts = content.split(maxsplit=1)
            if len(parts) < 2:
                await message.channel.send("Usage: |play <url or search term>")
                return
            query = parts[1].strip()
            
            if not query:
                await message.channel.send("Please provide a URL or search term.")
                return

            data = await _extract_info_safe(query)
            if not data:
                await message.channel.send("Failed to retrieve information for that query.")
                return
            if isinstance(data, dict) and data.get("error"):
                await message.channel.send(f"Error fetching info: {data.get('error')}")
                return

            entries = data.get("entries") if isinstance(data, dict) else None
            added_count = 0

            # If input is URL, prefer top-level metadata from data
            if _is_url(query):
                # single video metadata
                if isinstance(data, dict) and data.get("title") and not entries:
                    meta = _extract_metadata_from_entry(data)
                    if meta and meta["url"]:
                        async with state["lock"]:
                            state["queue"].append({
                                QUEUE_ITEM_KEY_SOURCE: meta["url"],
                                QUEUE_ITEM_KEY_TITLE: sanitize_filename(meta["title"])
                            })
                            added_count = 1

                        embed = _create_metadata_embed(
                            meta["title"], meta["description"], meta["uploader"],
                            meta["duration"], meta["views"], meta["thumbnail"]
                        )
                        await message.channel.send(embed=embed)
                else:
                    # playlist or multiple entries from URL
                    if entries:
                        first_valid = _extract_metadata_from_entry(next((e for e in entries if isinstance(e, dict)), None))
                        if first_valid:
                            embed = _create_metadata_embed(
                                first_valid["title"], first_valid["description"], first_valid["uploader"],
                                first_valid["duration"], first_valid["views"], first_valid["thumbnail"],
                                is_playlist=True
                            )
                            await message.channel.send(embed=embed)

                            count = 0
                            async with state["lock"]:
                                for entry in entries:
                                    if count >= PLAYLIST_ENTRY_LIMIT:
                                        break
                                    if not isinstance(entry, dict):
                                        continue
                                    meta = _extract_metadata_from_entry(entry)
                                    if meta and meta["url"]:
                                        state["queue"].append({
                                            QUEUE_ITEM_KEY_SOURCE: meta["url"],
                                            QUEUE_ITEM_KEY_TITLE: sanitize_filename(meta["title"])
                                        })
                                        count += 1
                            added_count = count

            # If input is search term, show metadata for first result and add entries
            else:
                if entries:
                    first_valid = _extract_metadata_from_entry(next((e for e in entries if isinstance(e, dict)), None))
                    if first_valid:
                        embed = _create_metadata_embed(
                            first_valid["title"], first_valid["description"], first_valid["uploader"],
                            first_valid["duration"], first_valid["views"], first_valid["thumbnail"]
                        )
                        await message.channel.send(embed=embed)

                        count = 0
                        async with state["lock"]:
                            for entry in entries:
                                if count >= PLAYLIST_ENTRY_LIMIT:
                                    break
                                meta = _extract_metadata_from_entry(entry)
                                if meta and meta["url"]:
                                    state["queue"].append({
                                        QUEUE_ITEM_KEY_SOURCE: meta["url"],
                                        QUEUE_ITEM_KEY_TITLE: sanitize_filename(meta["title"])
                                    })
                                    count += 1
                        added_count = count
                    else:
                        # fallback single item
                        meta = _extract_metadata_from_entry(data)
                        if meta and meta["url"]:
                            async with state["lock"]:
                                state["queue"].append({
                                    QUEUE_ITEM_KEY_SOURCE: meta["url"],
                                    QUEUE_ITEM_KEY_TITLE: sanitize_filename(meta["title"])
                                })
                                added_count = 1
                        else:
                            await message.channel.send("Could not extract audio URL from the provided link.")
                            return
                else:
                    # single item result from search
                    meta = _extract_metadata_from_entry(data)
                    if not meta or not meta["url"]:
                        await message.channel.send("Could not extract audio URL from the search result.")
                        return
                    async with state["lock"]:
                        state["queue"].append({
                            QUEUE_ITEM_KEY_SOURCE: meta["url"],
                            QUEUE_ITEM_KEY_TITLE: sanitize_filename(meta["title"])
                        })
                        added_count = 1

            # ensure default mode
            state["mode"] = state.get("mode", "none")

            # start playback if idle
            vc = state.get("vc")
            if vc and not vc.is_playing():
                client.loop.call_soon_threadsafe(play_next_song, guild_id)

            await message.channel.send(f"Added {added_count} item(s) to the queue.")

        # SKIP
        elif content.startswith("|skip"):
            try:
                gid = message.guild.id
                state = states.get(gid)
                if not state:
                    await message.add_reaction('❌')
                    return
                vc = state.get("vc")
                if vc and vc.is_playing():
                    vc.stop()
                    client.loop.call_soon_threadsafe(play_next_song, gid)
                    await message.add_reaction('⏭')
            except Exception:
                logger.exception("Error on skip")

        # PAUSE
        elif content.startswith("|pause"):
            try:
                gid = message.guild.id
                state = states.get(gid)
                vc = state.get("vc") if state else None
                if vc and vc.is_playing():
                    vc.pause()
                    await message.add_reaction('⏸')
                else:
                    await message.channel.send("Nothing is playing.")
            except Exception:
                logger.exception("Error on pause")

        # RESUME
        elif content.startswith("|resume"):
            try:
                gid = message.guild.id
                state = states.get(gid)
                vc = state.get("vc") if state else None
                if vc and vc.is_paused():
                    vc.resume()
                    await message.add_reaction('▶')
                else:
                    await message.channel.send("Nothing is paused.")
            except Exception:
                logger.exception("Error on resume")

        # STOP
        elif content.startswith("|stop"):
            try:
                gid = message.guild.id
                state = states.get(gid)
                if state:
                    vc = state.get("vc")
                    if vc:
                        vc.stop()
                        await vc.disconnect()
                        state["vc"] = None
                    state["channel_id"] = None
                    state["notify_channel_id"] = None
                    state["inactive_since"] = None
                    state["should_reconnect"] = False
                    state["queue"] = []
                    state["now_playing"] = None
                await message.add_reaction('⏹')
            except Exception:
                logger.exception("Error on stop")

        # LOOP
        elif content.startswith("|loop"):
            try:
                parts = content.split(maxsplit=1)
                mode = "none"
                if len(parts) > 1:
                    arg = parts[1].strip().lower()
                    if arg in ("none", "one", "all"):
                        mode = arg
                    else:
                        await message.channel.send("Loop mode must be one of: none, one, all.")
                        return
                gid = message.guild.id
                state = _ensure_state(gid)
                state["mode"] = mode
                emoji_map = {"none": "🔁", "one": "🔂", "all": "🔃"}
                await message.add_reaction(emoji_map.get(mode, "✅"))
            except Exception:
                logger.exception("Error on loop command")

        # QUEUE
        elif content.startswith("|queue"):
            try:
                gid = message.guild.id
                state = states.get(gid)
                q = state["queue"] if state else []
                if not q:
                    await message.channel.send("Queue is empty.")
                    return
                embed = discord.Embed(
                    title="📝 Queue",
                    description="Upcoming songs (max 10 shown):",
                    color=discord.Color.green()
                )
                lines = []
                for i, item in enumerate(q[:10], start=1):
                    lines.append(f"{i}. {item.get(QUEUE_ITEM_KEY_TITLE,'Unknown')}")
                embed.description += "\n" + "\n".join(lines)
                await message.channel.send(embed=embed)
            except Exception:
                logger.exception("Error on queue")

        # NOW
        elif content.startswith("|now"):
            try:
                gid = message.guild.id
                state = states.get(gid)
                item = state.get("now_playing") if state else None
                if not item:
                    await message.channel.send("Nothing is playing.")
                    return
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{item.get(QUEUE_ITEM_KEY_TITLE,'Unknown')}**",
                    color=discord.Color.purple()
                )
                await message.channel.send(embed=embed)
            except Exception:
                logger.exception("Error on now")

    client.run(TOKEN)

if __name__ == "__main__":
    run_bot()
