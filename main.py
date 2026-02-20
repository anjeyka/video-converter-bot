# Script that converts all videos sent to a specific group into MP4 format
import os
import asyncio
import re
import subprocess
import logging
import time
import json
from dotenv import load_dotenv, set_key
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaDocument, DocumentAttributeVideo, DocumentAttributeFilename
from asyncio import Queue

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Import API_ID, API_HASH, GROUP_ID (if missing in .env, default to 0)
ENV_PATH = "config.env"
load_dotenv(ENV_PATH)

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
GROUP_ID = int(os.getenv("GROUP_ID", 0))

# ============= SETTINGS =============
# Video settings

# Default width
DEFAULT_WIDTH = 720

# Default CRF (0–51, higher value = stronger compression and lower quality)
DEFAULT_CRF = 23

# Global variables for current width and CRF
current_width = DEFAULT_WIDTH
current_crf = DEFAULT_CRF

# Queue for video processing
video_queue = Queue()
is_processing = False

# Bot status on startup
ACTIVE = True

# Initialize the client; "bot" is the session file name (bot.session)
client = TelegramClient('bot', API_ID, API_HASH)

# Saving configuration
def rewrite_config_file(new_group_id):
    # set_key automatically finds or creates GROUP_ID without touching other lines
    set_key(ENV_PATH, "GROUP_ID", str(new_group_id))

# Retrieve video information
async def get_video_info(input_file):
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=width,height:format=duration',
            '-of', 'json', input_file
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if not stdout:
            raise ValueError(f"ffprobe returned no data: {stderr.decode()}")

        data = json.loads(stdout.decode())

        streams = data.get('streams', [])
        video_stream = next((s for s in streams if s.get('width')), {})

        raw_duration = data.get('format', {}).get('duration', 0)
        try:
            duration = float(raw_duration)
        except ValueError:
            duration = 0.0

        return {
            'width': int(video_stream.get('width', 0)),
            'height': int(video_stream.get('height', 0)),
            'duration': duration,
            'size_mb': os.path.getsize(input_file) / (1024 * 1024)
        }

    except Exception as e:
        print(f"ffprobe error: {e}")
        return None

# Convert video with progress tracking in Telegram
async def convert_video(input_file, output_file, target_width, crf_value, status_msg, info):
    try:
        duration_us = int(info['duration'] * 1000000)
        start_time = time.time()
        last_update_time = 0

        cmd = [
            'ffmpeg', '-i', input_file,
            '-c:v', 'libx264', '-profile:v', 'high',
            '-level', '4.0',
            '-crf', str(crf_value),
            '-preset', 'faster',

            # Remove unnecessary streams
            '-map', '0:v:0',           # Only the first video stream
            '-map', '0:a:0?',          # Only the first audio stream (if present)
            '-map_metadata', '-1',     # Remove global metadata
            '-map_chapters', '-1',     # Remove broken chapter data
            '-sn', '-dn',              # Remove subtitles and data streams
            
            '-vf', f"scale='min({target_width},iw)':-2,format=yuv420p",
            '-x264-params', 'vbv-maxrate=2500:vbv-bufsize=5000:keyint=60:min-keyint=10:scenecut=40:open-gop=0',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart', '-y',
            '-progress', 'pipe:1', '-nostats',  # Write progress to stdout
            output_file
        ]

        # Launch asynchronous subprocess
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            data = line.decode().strip()
            if "out_time_us=" in data:
                try:
                    current_time_us = int(data.split('=')[1])
                    percent = min(round((current_time_us / duration_us) * 100), 100)

                    now = time.time()
                    # Update Telegram every 10 seconds to avoid FloodWait
                    if now - last_update_time >= 10:
                        bar = create_progress_bar(percent)  # Function created earlier

                        # Calculate ETA (estimated time remaining)
                        elapsed = now - start_time
                        if percent > 0:
                            eta_val = (elapsed / (percent / 100)) - elapsed
                            eta_str = format_time(eta_val)
                        else:
                            eta_str = "calculating..."

                        await status_msg.edit(
                            f"🔄 **Converting video...**\n\n"
                            f"📊 `{bar}`\n"
                            f"⏳ Time left: ~`{eta_str}`\n"
                            f"📐 Width: `{target_width}px` | CRF: `{crf_value}`"
                        )
                        last_update_time = now
                except Exception as e:
                    logger.error(f"Progress parsing error: {e}")

        await process.wait()
        stderr = (await process.stderr.read()).decode()
        if process.returncode != 0:
            logger.error(f"FFMPEG ERROR:\n{stderr}")
        return process.returncode == 0

    except Exception as e:
        logger.error(f"Conversion error: {e}")
        return False

# Extract the file extension / filename
def get_video_filename(message):

    try:
        if message.video:
            # For video messages
            for attr in message.video.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    return attr.file_name
            return "video.mp4"

        elif message.document:
            # For documents
            for attr in message.document.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    return attr.file_name

            # If no filename, try to determine extension from MIME type
            mime = message.document.mime_type or ""
            if 'video' in mime:
                ext = mime.split('/')[-1]
                if ext in ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm', 'mpeg', 'mpg']:
                    return f"video.{ext}"

            return "video.mp4"

    except Exception as e:
        logger.error(f"Error retrieving filename: {e}")
        return "video.mp4"


async def download_video(message, file_path, status_msg):
    """Download video with a progress bar"""
    last_update_time = 0

    async def progress_callback(current, total):
        nonlocal last_update_time
        now = time.time()

        # Update status no more than once every 10 seconds
        if now - last_update_time < 10:
            return

        last_update_time = now
        percent = min(round((current / total) * 100), 100)
        bar = create_progress_bar(percent)  # Your progress bar drawing function

        try:
            await status_msg.edit(
                f"📥 **Downloading video...**\n"
                f"`{bar}`\n"
                f"📊 {current / (1024*1024):.1f} / {total / (1024*1024):.1f} MB"
            )
        except Exception:
            pass  # Ignore MessageNotModifiedError

    try:
        await message.download_media(file_path, progress_callback=progress_callback)
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False


async def process_video_from_queue():
    """Video queue processor with progress tracking (%)"""
    global is_processing

    while True:
        try:
            video_data = await video_queue.get()
            is_processing = True

            status_msg = None

            message = video_data['message']
            user_id = video_data['user_id']
            message_id = video_data['message_id']

            original_filename = get_video_filename(message)
            file_extension = os.path.splitext(original_filename)[1] or '.mp4'
            input_path = f"input_{user_id}_{message_id}{file_extension}"
            output_path = f"output_{user_id}_{message_id}.mp4"

            status_msg = await message.reply("⏳ Uploading video to server...")

            try:
                # 1. Download
                success = await download_video(message, input_path, status_msg)

                if not success:
                    await status_msg.edit("❌ Failed to download the video.")
                    continue

                # 2. Metadata
                info = await get_video_info(input_path)
                if not info:
                    await status_msg.edit("❌ Failed to read video metadata.")
                    continue

                await status_msg.edit(
                    f"🔄 **Preparing for conversion...**\n"
                    f"📦 Size: `{info['size_mb']:.1f} MB`\n"
                    f"📐 Original: `{info['width']}x{info['height']}`\n"
                    f"🎯 Target width: `{current_width}px` (CRF: {current_crf})"
                )

                # 3. Conversion
                success = await convert_video(
                    input_path,
                    output_path,
                    current_width,
                    current_crf,
                    status_msg,
                    info
                )

                if not success or not os.path.exists(output_path):
                    await status_msg.edit("❌ Conversion error.")
                    continue

                # 4. Analysis
                output_info = await get_video_info(output_path)
                thumb_path = f"thumb_{user_id}_{message_id}.jpg"
                has_thumb = await generate_thumb(output_path, thumb_path)
                output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
                actual_width = output_info['width'] if output_info else current_width

                # Add video attributes so Telegram recognizes the file properly
                from telethon.tl.types import DocumentAttributeVideo
                video_attributes = DocumentAttributeVideo(
                    duration=int(output_info['duration']),
                    w=output_info['width'],
                    h=output_info['height'],
                    supports_streaming=True
                )

                # Upload progress tracking
                last_send_update = 0

                async def send_progress_callback(current, total):
                    nonlocal last_send_update
                    now = time.time()
                    # Update status no more than once every 7 seconds
                    if now - last_send_update < 7:
                        return
                    last_send_update = now

                    percent = min(round((current / total) * 100), 100)
                    bar = create_progress_bar(percent)
                    try:
                        await status_msg.edit(
                            f"📤 **Uploading result...**\n"
                            f"`{bar}`\n"
                            f"📊 `{current / (1024*1024):.1f}` / `{total / (1024*1024):.1f}` MB"
                        )
                    except:
                        pass

                caption = (
                    f"✅ **Video converted!**\n\n"
                    f"📥 Input: `{info['size_mb']:.1f} MB` ({info['width']}x{info['height']})\n"
                    f"📤 Output: `{output_size_mb:.1f} MB` ({actual_width}x{output_info['height']})\n"
                    f"🎯 CRF: `{current_crf}`"
                )

                # Send file with progress bar
                await client.send_file(
                    GROUP_ID,
                    output_path,
                    caption=caption,
                    reply_to=message_id,
                    thumb=thumb_path if has_thumb else None,  # Attach thumbnail
                    attributes=[video_attributes],            # Attach metadata
                    supports_streaming=True,
                    progress_callback=send_progress_callback
                )
                
                await status_msg.delete()
                logger.info(f"Success! File: {original_filename}")

            except Exception as e:
                logger.error(f"Processing error: {e}")
                if status_msg:
                    await status_msg.edit(f"❌ Error: {str(e)}")

            finally:
                for path in [input_path, output_path]:
                    if os.path.exists(path):
                        os.remove(path)
                # Remove temporary thumbnail
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
                    
                video_queue.task_done()

        except Exception as e:
            logger.error(f"Critical queue error: {e}")

        finally:
            if video_queue.empty():
                is_processing = False


# Create_progress_bar
def create_progress_bar(percent):
    done = int(percent / 10)  # 10 characters in the bar (100 / 10 = 10)
    remain = 10 - done
    return f"[{'█' * done}{'░' * remain}] {percent}%"


# Convert seconds into MM:SS format
def format_time(seconds):
    if seconds <= 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


# Create a screenshot of the first video frame
async def generate_thumb(video_path, thumb_path):
    cmd = [
        'ffmpeg',
        '-noaccurate_seek',    # fast seek, decodes only the nearest keyframe
        '-ss', '5',            # seek to around 5 seconds (early frames may be black)
        '-i', video_path,      # path to the video
        '-vf', 'select=eq(pict_type\,I)',    # select the next I‑frame
        '-frames:v', '1',      # extract only 1 frame
        '-q:v', '2',           # JPEG quality (lower = better)
        '-y', thumb_path       # output thumbnail path, overwrite if exists
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await process.wait()
    return os.path.exists(thumb_path)
    
    
#################################
# command and message handling  #
#################################

# Video message handler
@client.on(events.NewMessage(chats=GROUP_ID))
async def handle_video_message(event):
    global ACTIVE
    
    # If conversion is paused — ignore the message
    if not ACTIVE:
        return
    
    message = event.message

    # Check if the message contains a video
    has_video = False

    if message.video:
        has_video = True
    elif message.document:
        # Check MIME type of the document
        if message.document.mime_type and 'video' in message.document.mime_type:
            has_video = True

    if not has_video:
        return

    # Ignore commands
    if message.text and message.text.startswith('/'):
        return

    # Add video to the queue
    video_data = {
        'message': message,
        'user_id': event.sender_id,
        'message_id': message.id
    }

    await video_queue.put(video_data)

    queue_size = video_queue.qsize()

    if queue_size > 1:
        await message.reply(
            f"📋 Added to processing queue\n"
            f"Queue position: {queue_size}"
        )
        logger.info(f"Video added to queue. Position: {queue_size}")
    else:
        logger.info(f"Video added to queue for immediate processing")


# Width change command handler
@client.on(events.NewMessage(chats=GROUP_ID, pattern=r'^/width (\d+)$'))
async def change_width(event):
    global current_width

    try:
        new_width = int(event.pattern_match.group(1))

        if 16 <= new_width <= 2048 and new_width % 2 == 0:
            current_width = new_width
            await event.reply(f"✅ Width set to {current_width}px")
            logger.info(f"Width changed to {current_width}px")
        else:
            await event.reply("❌ Error: value must be between 16 and 2048 and must be even")
            return

    except Exception as e:
        logger.error(f"Width change error: {e}")
        await event.reply(f"❌ Error: {str(e)}")


# CRF change command handler
@client.on(events.NewMessage(chats=GROUP_ID, pattern=r'^/crf (\d+)$'))
async def change_cfr(event):
    global current_crf

    try:
        new_crf = int(event.pattern_match.group(1))

        if new_crf < 0 or new_crf > 51:
            await event.reply(
                "❌ Please specify a value between 0 and 51,\n"
                "where 0 = highest quality and 51 = lowest quality."
            )
            return

        current_crf = new_crf
        await event.reply(f"✅ CRF set to {current_crf}")
        logger.info(f"CRF changed to {current_crf}")

    except Exception as e:
        logger.error(f"CRF change error: {e}")
        await event.reply(f"❌ Error: {str(e)}")


# Queue status command handler
@client.on(events.NewMessage(chats=GROUP_ID, pattern='^/queue$'))
async def queue_command(event):
    queue_size = video_queue.qsize()

    if queue_size == 0:
        status = "✅ Queue is empty"
        if is_processing:
            status += "\n🔄 Currently processing 1 video"
    else:
        status = f"📋 Videos in queue: {queue_size}"
        if is_processing:
            status += "\n🔄 Currently processing 1 video"

    await event.reply(status)

# Handler for /start command
@client.on(events.NewMessage(chats=GROUP_ID, pattern='^/start$'))
async def start_command(event):
    global ACTIVE
    ACTIVE = True
    await event.reply("⚡ Video conversion enabled")


# Handler for /stop command
@client.on(events.NewMessage(chats=GROUP_ID, pattern='^/stop$'))
async def stop_command(event):
    global ACTIVE
    ACTIVE = False
    await event.reply("💤 Video conversion paused")


# Handler for /status command
@client.on(events.NewMessage(chats=GROUP_ID, pattern='^/status$'))
async def status_command(event):
    global ACTIVE, current_width, current_crf

    status_text = "⚡ Enabled" if ACTIVE else "💤 Paused"

    width_text = (
        f"{current_width}px" if current_width is not None else "—"
    )

    crf_text = (
        str(current_crf) if current_crf is not None else "—"
    )

    await event.reply(
        f"📊 *System Status*\n"
        f"• State: {status_text}\n"
        f"• Current width: {width_text}\n"
        f"• Current CRF: {crf_text}",
        parse_mode="markdown"
    )


# Handler for /help command
@client.on(events.NewMessage(chats=GROUP_ID, pattern='^/help$'))
async def help_command(event):
    await event.reply(
        f"🚀 Video Converter Bot\n\n"
        f"I automatically convert all videos in this group!\n\n"
        f"📹 Settings:\n"
        f"• Current width: {current_width}px\n"
        f"• CRF value: {current_crf}\n\n"
        f"💬 Commands:\n"
        f"• `/start` — enable video conversion\n"
        f"• `/stop` — pause video conversion\n"
        f"• `/status` — show current status\n"
        f"• `/width 1280` — set width to 1280px\n"
        f"• `/crf 23` — set quality factor to 23\n"
        f"   (0 — highest quality, 51 — lowest)\n"
        f"• `/queue` — show processing queue\n"
        f"• `/help` — show this message\n\n"
        f"Just send a video and I will convert it! ✨"
    )


# group ID initialization
@client.on(events.NewMessage(pattern='^/setup_4phone$'))
async def setup_4phone(event):
    global GROUP_ID  # Allow modifying imported variables
    
    print(f"Received initialization command")
    
    # Check if the bot is not configured yet (ID is 0 or None)
    if not GROUP_ID or GROUP_ID == 0:
        print(f"new_id_group: {event.chat_id}")
        
        # 1. Update the .env file on disk
        rewrite_config_file(event.chat_id)

        # 2. Update in‑memory variables (so the bot continues working without restart)
        GROUP_ID = event.chat_id

        print(f"The bot will now respond to messages in this group.")
        await event.respond(
            f"⚙️ **Configuration saved!**\n\n"
            f"👥 Group: `{GROUP_ID}`\n"
            "The bot will now respond to messages in this group."
        )
    else:
        # If GROUP_ID already exists — show current configuration
        try:
            # Get chat entity for the stored ID
            current_chat = await client.get_entity(GROUP_ID)
            # For groups it's .title, for users it's .first_name
            chat_name = getattr(current_chat, 'title', getattr(current_chat, 'first_name', 'Unknown'))
        except Exception:
            chat_name = "Unable to access name"

        await event.respond(
            f"✅ **Bot is already configured:**\n"
            f"👥 Group: `{chat_name}`\n"
            f"🆔 ID: `{GROUP_ID}`"
        )

###############################
#    Main startup function    #
###############################
async def main():
    print("=" * 50)
    print("🤖 Telethon Video Converter Userbot")
    print("=" * 50)

    # Check ffmpeg
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        version = result.stdout.split('\n')[0]
        print(f"✅ FFmpeg: {version}")
    except:
        print("❌ ERROR: ffmpeg is not installed!")
        print("Install with: sudo apt-get install ffmpeg")
        return

    # Connect to Telegram
    print("\n🔐 Connecting to Telegram...")
    await client.start()

    me = await client.get_me()
    print(f"✅ Logged in as: {me.first_name} (@{me.username})")

    # Start queue processor
    asyncio.create_task(process_video_from_queue())

    # Start client
    if GROUP_ID and str(GROUP_ID) != "0":
        print(f"✅ Group ID: {GROUP_ID}")
        await client.send_message(
            GROUP_ID,
            f"🚀 Video Converter Bot started!\n"
            f"Just send a video and I will convert it! ✨\n\n"
            f"Send `/help` for instructions"
        )
    else:
        print("Group ID not set")
        print("Send /setup_4phone in the desired group")

    print(f"✅ Current width: {current_width}px")
    print(f"✅ Quality factor (CRF): {current_crf}")
    print("=" * 50)
    print("🚀 Userbot is running and waiting for videos!")
    print("💡 Press Ctrl+C to stop")
    print("=" * 50)

    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n\n👋 Userbot stopped!")
    except Exception as e:
        logger.error(f"Critical error: {e}")
        print(f"\n❌ Error: {e}")