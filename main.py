import googleapiclient.discovery
from googleapiclient.discovery import build
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context, make_response

from pytube import YouTube as YT
from youtubesearchpython import VideosSearch
import os
import re
import uuid
import time
from flask_cors import cross_origin, CORS
import json
from threading import Thread
import threading
import yt_dlp as youtube_dl
from pydub import AudioSegment
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
cors = CORS(app)

# Define the retention period in seconds (e.g., 2 hours)
RETENTION_PERIOD = 2 * 60 * 60

# Configure rate limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["30 per second"],
    storage_uri="memory://",
)


@app.route('/')
def nothing():
    response = jsonify({'msg': 'Use /download or /audios/<filename>'})
    response.headers.add('Content-Type', 'application/json')
    return response


def compress_audio(file_path):
    audio = AudioSegment.from_file(file_path)
    compressed_audio = audio.export(file_path, format='mp3', bitrate='256k')
    compressed_audio.close()


def generate(host_url, video_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'audios/%(id)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '256',
            'nopostoverwrites': True  # Avoid overwriting manually converted MP3 files
        }],
        'verbose': True  # Enable verbose output for debugging
    }

    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=False)
        duration = info_dict.get('duration')

        if duration and duration <= 300:  # Check if video duration is <= 5 minutes (300 seconds)
            info_dict = ydl.extract_info(video_url, download=True)
            audio_file_path = ydl.prepare_filename(info_dict)
            thumbnail_url = info_dict.get('thumbnail')

            file_name, file_extension = os.path.splitext(audio_file_path)
            file_name = os.path.basename(file_name)
            expiration_timestamp = int(time.time()) + RETENTION_PERIOD

            compress_audio("audios/" + file_name + ".mp3")

            response_dict = {
                'img': thumbnail_url,
                'direct_link': host_url + "audios/" + file_name + ".mp3",
                'expiration_timestamp': expiration_timestamp
            }
            response_json = json.dumps(response_dict)
            response_bytes = response_json.encode('utf-8')

            with app.app_context():
                yield response_bytes
        else:
            response_dict = {
                'error': 'Video duration must be less than or equal to 5 minutes.'
            }
            response_json = json.dumps(response_dict)
            response_bytes = response_json.encode('utf-8')
            yield response_bytes


# IMPORTANT: Replace with your actual YouTube Data API v3 key
API_KEY = "AIzaSyBk1K2qCYB52-_ANWjTUyItVCv8y9wiqKc"


@app.route('/search', methods=['GET'])
@limiter.limit("5/minute", error_message="Too many requests")
def search():
    q = request.args.get('q')
    if not q:
        return jsonify({'error': 'Invalid search query'}), 400

    try:
        youtube = build("youtube", "v3", developerKey=API_KEY)

        request_yt = youtube.search().list(
            part="snippet",
            maxResults=15,
            q=q,
            type="video"
        )
        response = request_yt.execute()

        search_results = []
        for item in response.get("items", []):
            video_data = {
                'title': item['snippet']['title'],
                'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                'thumbnail': item['snippet']['thumbnails']['default']['url']
            }
            search_results.append(video_data)

        return jsonify({'search': search_results})

    except Exception as e:
        print(f"Error during YouTube search: {e}")  # Print error for debugging
        return jsonify({'error': 'An error occurred during the search'}), 500


@app.route('/download', methods=['GET'])
@limiter.limit("5/minute", error_message="Too many requests")  # Limit to 5 requests per minute
def download_audio():
    video_url = request.args.get('video_url')  # Get the video URL from the request

    # 🛠 DEBUG: Print the received URL to Railway logs
    print("🚀 Received video URL:", video_url)

    # If no URL is received, return an error response
    if not video_url:
        return {"error": "No video_url received"}, 400

    host_url = request.base_url + '/'
    return Response(stream_with_context(generate(host_url, video_url)), mimetype='application/json')


@app.route('/audios/<path:filename>', methods=['GET'])
@limiter.limit("2/5seconds", error_message="Too many requests")  # Limit to 2 requests per 30 seconds
def serve_audio(filename):
    root_dir = os.getcwd()
    file_path = os.path.join(root_dir, 'audios', filename)

    if not os.path.isfile(file_path):
        return make_response('Audio file not found', 404)

    return send_from_directory(root_dir, file_path, as_attachment=True)


def delete_expired_files():
    current_timestamp = int(time.time())

    for file_name in os.listdir('audios'):
        file_path = os.path.join('audios', file_name)
        print("current time", current_timestamp, "time file:", os.path.getmtime(file_path) + RETENTION_PERIOD)
        if os.path.isfile(file_path) and current_timestamp > os.path.getmtime(file_path) + RETENTION_PERIOD:
            os.remove(file_path)


def delete_files_task():
    if not os.path.exists('audios'):
        os.makedirs('audios')
    delete_expired_files()
    threading.Timer(100, delete_files_task).start()


if __name__ == '__main__':
    app.run(host='0.0.0.0')
