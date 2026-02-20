Telegram Video Converter Userbot
A lightweight and fully automated Telegram userbot that converts all videos sent to a specific group into MP4 format using FFmpeg.
The bot listens for new messages, queues incoming videos, converts them with predefined parameters (width, CRF), and sends the processed file back to the chat.
This project is ideal for optimizing videos for mobile devices, reducing file size, or ensuring consistent playback compatibility across Telegram clients.

✨ Features
• 	🔄 Automatic video conversion for all videos sent to the configured group
• 	🎚️ Adjustable width and CRF (quality) parameters
• 	📥 Queue-based processing — videos are handled one by one
• 	⚙️ Dynamic configuration via Telegram commands
• 	🧩 FFmpeg-based transcoding with safe defaults for Telegram compatibility
• 	🔐 Works as a Telethon userbot (runs under your own account)
• 	💬 Sends progress updates and status messages

🛠️ Technologies Used
• 	Python 3
• 	Telethon (Telegram API client)
• 	FFmpeg (video transcoding)
• 	asyncio (asynchronous queue processing)
• 	dotenv (environment configuration)

📦 Installation
git clone https://github.com/anjeyka/video-converter-bot.git
cd video-converter-bot
pip install -r requirements.txt

Make sure FFmpeg is installed:
sudo apt-get install ffmpeg

Configure config.env file with:
# 🔐 Telegram Client ID
# can be obtained here: https://my.telegram.org/apps
API_ID=your_api_id
API_HASH=your_api_hash
# Group ID (Run the /setup_4phone command in the group for automatic setup)
GROUP_ID=0


▶️ Usage
Start the bot:
python main.py

If the bot is not yet linked to a group, send the command below inside the target group:
/setup_4phone
After that, the bot will automatically react to all incoming videos.

💬 Send /help to see available commands

📁 Project Structure
project/
│── main.py
│── config.env
│── README.md
└── requirements.txt

⚙️ How It Works
1. 	The bot listens for new messages in the configured group.
2. 	When a video is detected, it is added to an asynchronous queue.
3. 	The queue processor downloads the file and runs FFmpeg with the selected width and CRF.
4. 	The converted MP4 is uploaded back to the group.
5. 	The bot updates progress and status messages during conversion.

🧪 Requirements
• 	Python 3.9+
• 	FFmpeg installed and available in PATH
• 	Telegram API credentials (API ID & API Hash)

📜 License
This project is released under the MIT License.

🤝 Contributing
Pull requests and feature suggestions are welcome.
Feel free to open an issue if you encounter a bug or want to propose improvements.