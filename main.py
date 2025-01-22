import os
import time
import logging
import threading
from urllib.parse import urlparse, parse_qs

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp as youtube_dl
from pydub import AudioSegment

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
cors = CORS(app)

RETENTION_PERIOD = 2 * 60 * 60  # 2 hours

# Ensure audios directory exists
AUDIOS_DIR = 'audios'
os.makedirs(AUDIOS_DIR, exist_ok=True)

# Configure rate limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["30 per second"],
    storage_uri="memory://"
)

def compress_audio(file_path):
    """Compress audio file to MP3 format with reasonable quality"""
    try:
        audio = AudioSegment.from_file(file_path)
        compressed_path = file_path.rsplit('.', 1)[0] + '.mp3'
        audio.export(compressed_path, format='mp3', bitrate='256k')
        # Remove original file
        if compressed_path != file_path:
            os.remove(file_path)
        return compressed_path
    except Exception as e:
        logger.error(f"Audio compression error: {e}")
        raise

def get_video_id(url):
    """Extract video ID from YouTube URL"""
    try:
        parsed_url = urlparse(url)
        if parsed_url.hostname in ('www.youtube.com', 'youtube.com'):
            if parsed_url.path == '/watch':
                return parse_qs(parsed_url.query)['v'][0]
        elif parsed_url.hostname in ('youtu.be'):
            return parsed_url.path[1:]
        return None
    except Exception as e:
        logger.error(f"Video ID extraction error: {e}")
        return None

@app.route('/')
def home():
    return jsonify({
        'status': 'ok', 
        'message': 'Audio extraction service. Use /download endpoint.'
    })

@app.route('/download')
@limiter.limit("5/minute")
def download_audio():
    video_url = request.args.get('videoUrl')
    
    if not video_url:
        logger.warning("No video URL provided")
        return jsonify({"error": "No videoUrl provided"}), 400
    
    try:
        # Extract video ID for filename
        video_id = get_video_id(video_url)
        if not video_id:
            logger.warning(f"Invalid YouTube URL: {video_url}")
            return jsonify({"error": "Invalid YouTube URL"}), 400

        # Prepare output path
        output_template = os.path.join(AUDIOS_DIR, f'{video_id}_%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '256',
            }],
        }

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            # First check video duration
            info = ydl.extract_info(video_url, download=False)
            duration = info.get('duration', 0)

            if duration > 300:  # 5 minutes
                logger.warning(f"Video too long: {duration} seconds")
                return jsonify({
                    "error": "Video duration must be less than or equal to 5 minutes"
                }), 400

            # Download and process the audio
            ydl.download([video_url])
            
            # Find the downloaded file
            for file in os.listdir(AUDIOS_DIR):
                if file.startswith(video_id) and file.endswith('.mp3'):
                    audio_filename = file
                    break
            else:
                logger.error("No audio file found after download")
                return jsonify({"error": "Failed to download audio"}), 500
            
            # Construct the audio URL
            host_url = request.host_url.rstrip('/')
            audio_url = f"{host_url}/audios/{audio_filename}"
            
            return jsonify({
                "audioUrl": audio_url,
                "expirationTimestamp": int(time.time()) + RETENTION_PERIOD
            })

    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/audios/<path:filename>')
@limiter.limit("2/5seconds")
def serve_audio(filename):
    """Serve audio files with proper headers"""
    try:
        return send_from_directory(
            AUDIOS_DIR,
            filename,
            as_attachment=True,
            mimetype='audio/mpeg'
        )
    except FileNotFoundError:
        logger.warning(f"Audio file not found: {filename}")
        return jsonify({"error": "Audio file not found"}), 404

def delete_expired_files():
    """Remove audio files that have exceeded the retention period"""
    current_time = time.time()
    
    try:
        if os.path.exists(AUDIOS_DIR):
            for filename in os.listdir(AUDIOS_DIR):
                file_path = os.path.join(AUDIOS_DIR, filename)
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > RETENTION_PERIOD:
                        try:
                            os.remove(file_path)
                            logger.info(f"Deleted expired file: {filename}")
                        except Exception as e:
                            logger.error(f"Error deleting {filename}: {e}")
    except Exception as e:
        logger.error(f"Error in file cleanup: {e}")

def cleanup_task():
    """Periodic cleanup task"""
    while True:
        try:
            delete_expired_files()
            time.sleep(300)  # Check every 5 minutes
        except Exception as e:
            logger.error(f"Cleanup task error: {e}")
            time.sleep(300)

def initialize_app():
    """Initialize the application"""
    # Ensure audios directory exists
    os.makedirs(AUDIOS_DIR, exist_ok=True)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()

# Ensure app is initialized before serving
initialize_app()

# Gunicorn will call this
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))