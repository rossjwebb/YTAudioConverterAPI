from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import time
import json
import requests
from urllib.parse import urlparse, parse_qs

app = Flask(__name__)
cors = CORS(app)

RETENTION_PERIOD = 2 * 60 * 60  # 2 hours
TERMINAL_API_KEY = os.getenv('TERMINAL_API_KEY')

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["30 per second"],
    storage_uri="memory://"
)

@app.route('/')
def nothing():
    response = jsonify({'msg': 'Audio extraction service'})
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
        # Call Terminal API
        terminal_url = "https://web-production-ef6a6.up.railway.app/download"
        headers = {
            "Authorization": f"Bearer {TERMINAL_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "url": video_url
        }
        
        print("🔄 Calling Terminal API...")
        response = requests.post(terminal_url, headers=headers, json=data)
        
        if response.status_code != 200:
            print(f"❌ Terminal API error: {response.text}")
            return jsonify({"error": f"Terminal API error: {response.text}"}), response.status_code
            
        result = response.json()
        print(f"✅ Terminal API response: {result}")
        
        return jsonify({
            "audioUrl": result.get('audioUrl'),
            "expirationTimestamp": int(time.time()) + RETENTION_PERIOD
        })

    except Exception as e:
        print(f"❌ Error processing video: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))