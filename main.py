from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context, make_response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp as youtube_dl
from pydub import AudioSegment
import os
import time
import json
import threading
from urllib.parse import urlparse, parse_qs

app = Flask(__name__)
cors = CORS(app)

RETENTION_PERIOD = 2 * 60 * 60  # 2 hours

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["30 per second"],
    storage_uri="memory://"
)

def compress_audio(file_path):
    """Compress audio file to MP3 format with reasonable quality"""
    print(f"🎵 Compressing audio file: {file_path}")
    try:
        audio = AudioSegment.from_file(file_path)
        compressed_audio = audio.export(file_path, format='mp3', bitrate='256k')
        compressed_audio.close()
        print(f"✅ Audio compression complete: {file_path}")
    except Exception as e:
        print(f"❌ Error compressing audio: {str(e)}")
        raise e

def get_video_id(url):
    """Extract video ID from YouTube URL"""
    parsed_url = urlparse(url)
    if parsed_url.hostname in ('www.youtube.com', 'youtube.com'):
        if parsed_url.path == '/watch':
            return parse_qs(parsed_url.query)['v'][0]
    elif parsed_url.hostname in ('youtu.be'):
        return parsed_url.path[1:]
    return None

@app.route('/')
def nothing():
    response = jsonify({'msg': 'Use /download or /audios/<filename>'})
    response.headers.add('Content-Type', 'application/json')
    return response

@app.route('/download')
@limiter.limit("5/minute")
def download_audio():
    video_url = request.args.get('videoUrl')
    print(f"📥 Received request for URL: {video_url}")
    
    if not video_url:
        print("❌ No videoUrl provided")
        return jsonify({"error": "No videoUrl provided"}), 400
    
    try:
        # Extract video ID for filename
        video_id = get_video_id(video_url)
        if not video_id:
            print("❌ Invalid YouTube URL")
            return jsonify({"error": "Invalid YouTube URL"}), 400

        print(f"📝 Processing video ID: {video_id}")

        # Ensure audios directory exists
        if not os.path.exists('audios'):
            print("📁 Creating audios directory")
            os.makedirs('audios')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'audios/{video_id}.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '256',
            }],
        }

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            print("🔍 Checking video info...")
            # First check video duration
            info = ydl.extract_info(video_url, download=False)
            duration = info.get('duration', 0)

            if duration > 300:  # 5 minutes
                print("❌ Video too long")
                return jsonify({
                    "error": "Video duration must be less than or equal to 5 minutes"
                }), 400

            print("⬇️ Downloading audio...")
            # Download and process the audio
            ydl.download([video_url])
            
            # Construct the audio file path
            audio_filename = f"{video_id}.mp3"
            audio_path = os.path.join('audios', audio_filename)
            
            print(f"🔍 Checking if file exists at: {audio_path}")
            if not os.path.exists(audio_path):
                print(f"❌ Audio file not found at: {audio_path}")
                return jsonify({"error": "Audio file not created"}), 500
            
            # Compress the audio
            compress_audio(audio_path)
            
            # Double check file exists after compression
            if not os.path.exists(audio_path):
                print(f"❌ Audio file not found after compression: {audio_path}")
                return jsonify({"error": "Audio file lost after compression"}), 500
            
            # Construct the audio URL
            host_url = request.host_url.rstrip('/')
            audio_url = f"{host_url}/audios/{audio_filename}"
            
            print(f"✅ Successfully processed audio. URL: {audio_url}")
            return jsonify({
                "audioUrl": audio_url,
                "expirationTimestamp": int(time.time()) + RETENTION_PERIOD
            })

    except Exception as e:
        print(f"❌ Error processing video: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/audios/<path:filename>')
@limiter.limit("2/5seconds")
def serve_audio(filename):
    """Serve audio files with proper headers"""
    print(f"🎵 Request to serve audio file: {filename}")
    try:
        # Get absolute path
        root_dir = os.getcwd()
        file_path = os.path.join(root_dir, 'audios', filename)
        print(f"🔍 Looking for file at: {file_path}")
        
        if not os.path.exists(file_path):
            print(f"❌ File not found: {file_path}")
            return jsonify({"error": "Audio file not found"}), 404

        print(f"✅ Serving file: {file_path}")
        return send_from_directory(
            'audios',
            filename,
            as_attachment=True,
            mimetype='audio/mpeg'
        )
    except FileNotFoundError:
        print(f"❌ FileNotFoundError for: {filename}")
        return jsonify({"error": "Audio file not found"}), 404
    except Exception as e:
        print(f"❌ Error serving file: {str(e)}")
        return jsonify({"error": "Error serving file"}), 500

def delete_expired_files():
    """Remove audio files that have exceeded the retention period"""
    current_time = time.time()
    print("🧹 Checking for expired files...")
    if os.path.exists('audios'):
        for filename in os.listdir('audios'):
            file_path = os.path.join('audios', filename)
            if os.path.isfile(file_path):
                file_age = current_time - os.path.getmtime(file_path)
                if file_age > RETENTION_PERIOD:
                    try:
                        os.remove(file_path)
                        print(f"🗑️ Deleted expired file: {filename}")
                    except Exception as e:
                        print(f"❌ Error deleting {filename}: {str(e)}")

def cleanup_task():
    """Periodic cleanup task"""
    while True:
        delete_expired_files()
        time.sleep(300)  # Check every 5 minutes

def initialize_app():
    """Initialize the application"""
    print("🚀 Initializing application...")
    if not os.path.exists('audios'):
        print("📁 Creating audios directory")
        os.makedirs('audios')
    
    print("🧵 Starting cleanup thread")
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()
    print("✅ Initialization complete")

if __name__ == '__main__':
    initialize_app()  # Call initialization before running the app
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))