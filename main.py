import os
import time
import tempfile
import json
from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from flask_cors import CORS
from src.claude3_video_analyzer import VideoAnalyzer

app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')
CORS(app)

# アップロードされた動画を保存するディレクトリ
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'resources')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

analyzer = VideoAnalyzer()

@app.route('/')
def index():
    """メインページを表示"""
    return render_template('index.html', default_prompt=analyzer.default_prompt)

@app.route('/api/analyze', methods=['POST'])
def analyze_video():
    """動画を解析するAPI"""
    if 'video' not in request.files:
        return jsonify({'error': 'ビデオファイルがアップロードされていません'}), 400
        
    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400
        
    prompt = request.form.get('prompt', analyzer.default_prompt)
    
    # 一時ファイルに保存
    _, temp_path = tempfile.mkstemp(suffix='.mp4')
    video_file.save(temp_path)
    
    try:
        # フレームの取得
        base64_frames, _ = analyzer.get_frames_from_video(temp_path)
        
        def generate():
            """ストリーミングレスポンスを生成"""
            try:
                # プログレス通知
                progress_text = '動画フレームの抽出が完了しました。解析を開始します...\n\n'
                yield f"data: {json.dumps({'text': progress_text})}\n\n"
                
                # 予め取得したフレームを解析（非ストリーミング）
                if not analyzer.use_bedrock:
                    # Claude APIにリクエストを送信（Anthropicクライアント）
                    with analyzer.client.messages.stream(
                        model=analyzer.model,
                        max_tokens=1024,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    *map(lambda x: {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": x}}, base64_frames),
                                    {
                                        "type": "text",
                                        "text": prompt
                                    }
                                ],
                            }
                        ],
                    ) as stream:
                        for text in stream.text_stream:
                            yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    # Bedrock APIにリクエストを送信
                    body = json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 1024,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    *map(lambda x: {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": x}}, base64_frames),
                                    {
                                        "type": "text",
                                        "text": prompt
                                    }
                                ]
                            }
                        ]
                    })
                    
                    # Bedrockにストリーミングリクエストを送信
                    response = analyzer.bedrock_runtime.invoke_model_with_response_stream(
                        modelId=analyzer.model,
                        body=body
                    )
                    
                    # レスポンスストリームを処理
                    for event in response.get("body"):
                        if "chunk" in event:
                            chunk = json.loads(event["chunk"]["bytes"])
                            if "type" in chunk and chunk["type"] == "content_block_delta" and "delta" in chunk:
                                text = chunk["delta"].get("text", "")
                                if text:
                                    yield f"data: {json.dumps({'text': text})}\n\n"
                
                # 完了通知
                yield f"data: {json.dumps({'complete': True})}\n\n"
            
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                # 一時ファイルを削除
                try:
                    os.remove(temp_path)
                except:
                    pass
        
        return Response(generate(), mimetype='text/event-stream')
        
    except Exception as e:
        # 一時ファイルを削除
        try:
            os.remove(temp_path)
        except:
            pass
        return jsonify({'error': str(e)}), 500

@app.route('/static/<path:path>')
def serve_static(path):
    """静的ファイルを提供"""
    return send_from_directory('static', path)

if __name__ == "__main__":
    # クエリ文字列で直接実行をサポート
    print("Claude3 Video Analyzer Webサーバーを起動します...")
    print(f"使用モデル: {analyzer.model}")
    print(f"使用モード: {'Bedrock' if analyzer.use_bedrock else 'Anthropic Direct'}")
    print("http://localhost:5000/ にアクセスしてください")
    
    app.run(debug=True, host='0.0.0.0', port=5000)