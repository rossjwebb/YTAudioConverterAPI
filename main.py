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
    audio = AudioSegment.from_file(file_path)
    compressed_audio = audio.export(file_path, format='mp3', bitrate='256k')
    compressed_audio.close()

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
    
    if not video_url:
        return jsonify({"error": "No videoUrl provided"}), 400
    
    try:
        # Extract video ID for filename
        video_id = get_video_id(video_url)
        if not video_id:
            return jsonify({"error": "Invalid YouTube URL"}), 400

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
            # First check video duration
            info = ydl.extract_info(video_url, download=False)
            duration = info.get('duration', 0)

            if duration > 300:  # 5 minutes
                return jsonify({
                    "error": "Video duration must be less than or equal to 5 minutes"
                }), 400

            # Download and process the audio
            ydl.download([video_url])
            
            # Construct the audio file path
            audio_filename = f"{video_id}.mp3"
            audio_path = os.path.join('audios', audio_filename)
            
            # Compress the audio
            compress_audio(audio_path)
            
            # Construct the audio URL
            host_url = request.host_url.rstrip('/')
            audio_url = f"{host_url}/audios/{audio_filename}"
            
            return jsonify({
                "audioUrl": audio_url,
                "expirationTimestamp": int(time.time()) + RETENTION_PERIOD
            })

    except Exception as e:
        print(f"Error processing video: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/audios/<path:filename>')
@limiter.limit("2/5seconds")
def serve_audio(filename):
    """Serve audio files with proper headers"""
    try:
        return send_from_directory(
            'audios',
            filename,
            as_attachment=True,
            mimetype='audio/mpeg'
        )
    except FileNotFoundError:
        return jsonify({"error": "Audio file not found"}), 404

def delete_expired_files():
    """Remove audio files that have exceeded the retention period"""
    current_time = time.time()
    if os.path.exists('audios'):
        for filename in os.listdir('audios'):
            file_path = os.path.join('audios', filename)
            if os.path.isfile(file_path):
                file_age = current_time - os.path.getmtime(file_path)
                if file_age > RETENTION_PERIOD:
                    try:
                        os.remove(file_path)
                        print(f"Deleted expired file: {filename}")
                    except Exception as e:
                        print(f"Error deleting {filename}: {str(e)}")

def cleanup_task():
    """Periodic cleanup task"""
    while True:
        delete_expired_files()
        time.sleep(300)  # Check every 5 minutes

def initialize_app():
    """Initialize the application"""
    if not os.path.exists('audios'):
        os.makedirs('audios')
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()

if __name__ == '__main__':
    initialize_app()  # Call initialization before running the app
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))