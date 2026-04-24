import subprocess
import json


def yt_dlp_with_fallback(url):
    clients = ['android', 'tv_embedded', 'web']
    errors = []
    client_used = None

    for client in clients:
        command = [
            'yt-dlp',
            '--sleep-requests', '2',
            '--retries', '10',
            '--fragment-retries', '10',
            '--extractor-args', f'youtube:player_client={client}',
            '--user-agent', 'com.google.android.youtube/17.31.35 (Linux; U; Android 11)',
            url
        ]
        try:
            result = subprocess.run(command, capture_output=True, check=True, text=True)
            client_used = client
            return json.loads(result.stdout), client_used
        except subprocess.CalledProcessError as e:
            errors.append(f'Client: {client}, Error: {e.stderr}')  # Collect error for debugging

    raise Exception(f'All clients failed: {errors}')  # Report all failures


def get_info(url):
    try:
        info, client_used = yt_dlp_with_fallback(url)
        return {'info': info, 'client_used': client_used}
    except Exception as e:
        return {'error': str(e), 'client_used': None}


def run_download(url):
    try:
        download_info, client_used = yt_dlp_with_fallback(url)
        return {'status': 'success', 'download_info': download_info, 'client_used': client_used}
    except Exception as e:
        return {'status': 'error', 'error': str(e), 'client_used': None}