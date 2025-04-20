import os
import time
import tempfile
import json
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    send_from_directory,
    session,
)
from flask_cors import CORS
from src.claude3_video_analyzer import VideoAnalyzer
from goose_lib.api import goose_bp

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())
CORS(app)

# Goose API Blueprintを登録
app.register_blueprint(goose_bp)

# アップロードされた動画を保存するディレクトリ
UPLOAD_FOLDER = os.path.join(os.getcwd(), "resources")
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# 環境変数の読み込みを確認
from dotenv import load_dotenv

load_dotenv()  # 明示的に.envを読み込む

# VideoAnalyzerインスタンスの作成
try:
    analyzer = VideoAnalyzer()
    print(f"モード: {analyzer.mode}, Bedrock使用: {analyzer.use_bedrock}")
except Exception as e:
    print(f"初期化エラー: {str(e)}")
    raise


@app.route("/")
def index():
    """メインページを表示"""
    return render_template(
        "index.html",
        default_prompt=analyzer.default_prompt,
        default_chapters_prompt=analyzer.default_chapters_prompt,
    )


@app.route("/api/analyze", methods=["POST"])
def analyze_video():
    """動画を解析するAPI"""
    if "video" not in request.files:
        return jsonify({"error": "ビデオファイルがアップロードされていません"}), 400

    video_file = request.files["video"]
    if video_file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400

    prompt = request.form.get("prompt", analyzer.default_prompt)
    analyze_type = request.form.get("analyze_type", "normal")

    # 一時ファイルに保存
    _, temp_path = tempfile.mkstemp(suffix=".mp4")
    video_file.save(temp_path)

    try:
        # フレームの取得
        base64_frames, _ = analyzer.get_frames_from_video(temp_path)

        def generate():
            """ストリーミングレスポンスを生成"""
            try:
                # プログレス通知
                progress_text = (
                    "動画フレームの抽出が完了しました。解析を開始します...\n\n"
                )
                yield f"data: {json.dumps({'text': progress_text})}\n\n"

                # 解析タイプに基づいた処理
                # 章立てエンドポイントにリダイレクト
                if analyze_type == "chapters":
                    # この部分はもう使われない - フロントエンドが直接 /api/analyze/chapters を呼び出す
                    # このエンドポイントでは通常の解析のみを処理し、章立てはリダイレクトする
                    redirect_text = "章立て解析は専用のエンドポイントで処理されます。別のAPIを呼び出してください。"
                    yield f"data: {json.dumps({'text': redirect_text})}\n\n"
                    yield f"data: {json.dumps({'complete': True})}\n\n"
                    return

                    # 以下のコードは使用されないのでコメントアウト

                else:
                    # 通常の解析
                    if not analyzer.use_bedrock:
                        # Claude APIにリクエストを送信（Anthropicクライアント）
                        with analyzer.client.messages.stream(
                            model=analyzer.model,
                            max_tokens=1024,
                            messages=[
                                {
                                    "role": "user",
                                    "content": [
                                        *map(
                                            lambda x: {
                                                "type": "image",
                                                "source": {
                                                    "type": "base64",
                                                    "media_type": "image/jpeg",
                                                    "data": x,
                                                },
                                            },
                                            base64_frames,
                                        ),
                                        {"type": "text", "text": prompt},
                                    ],
                                }
                            ],
                        ) as stream:
                            for text in stream.text_stream:
                                yield f"data: {json.dumps({'text': text})}\n\n"
                    else:
                        # Bedrock APIにリクエストを送信
                        body = json.dumps(
                            {
                                "anthropic_version": "bedrock-2023-05-31",
                                "max_tokens": 1024,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": [
                                            *map(
                                                lambda x: {
                                                    "type": "image",
                                                    "source": {
                                                        "type": "base64",
                                                        "media_type": "image/jpeg",
                                                        "data": x,
                                                    },
                                                },
                                                base64_frames,
                                            ),
                                            {"type": "text", "text": prompt},
                                        ],
                                    }
                                ],
                            }
                        )

                        # Bedrockにストリーミングリクエストを送信
                        response = (
                            analyzer.bedrock_runtime.invoke_model_with_response_stream(
                                modelId=analyzer.model, body=body
                            )
                        )

                        # レスポンスストリームを処理
                        for event in response.get("body"):
                            if "chunk" in event:
                                chunk = json.loads(event["chunk"]["bytes"])
                                if (
                                    "type" in chunk
                                    and chunk["type"] == "content_block_delta"
                                    and "delta" in chunk
                                ):
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

        return Response(generate(), mimetype="text/event-stream")

    except Exception as e:
        # 一時ファイルを削除
        try:
            os.remove(temp_path)
        except:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze/chapters", methods=["POST"])
def analyze_video_with_chapters():
    """動画を章立て形式で解析するAPI"""
    if "video" not in request.files:
        return jsonify({"error": "ビデオファイルがアップロードされていません"}), 400

    video_file = request.files["video"]
    if video_file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400

    prompt = request.form.get("prompt", analyzer.default_chapters_prompt)

    # 一時ファイルに保存
    _, temp_path = tempfile.mkstemp(suffix=".mp4")
    video_file.save(temp_path)

    try:
        # フレームの取得（先に取得しておく）
        base64_frames, _ = analyzer.get_frames_from_video(temp_path)

        def generate():
            """ストリーミングレスポンスを生成"""
            try:
                # プログレス通知
                progress_text = (
                    "動画フレームの抽出が完了しました。章立て解析を開始します...\n\n"
                )
                yield f"data: {json.dumps({'text': progress_text})}\n\n"

                # 結果を保存する変数
                result_text = ""

                # 分岐: Anthropic APIかBedrock APIかによって処理を変更
                if not analyzer.use_bedrock:
                    # Anthropic API - クライアント直接利用
                    with analyzer.client.messages.stream(
                        model=analyzer.model,
                        max_tokens=2048,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    *map(
                                        lambda x: {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": "image/jpeg",
                                                "data": x,
                                            },
                                        },
                                        base64_frames,
                                    ),
                                    {"type": "text", "text": prompt},
                                ],
                            }
                        ],
                    ) as stream:
                        for text in stream.text_stream:
                            result_text += text
                            yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    # Bedrock API - ストリーミングAPI呼び出し
                    body = json.dumps(
                        {
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 2048,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        *map(
                                            lambda x: {
                                                "type": "image",
                                                "source": {
                                                    "type": "base64",
                                                    "media_type": "image/jpeg",
                                                    "data": x,
                                                },
                                            },
                                            base64_frames,
                                        ),
                                        {"type": "text", "text": prompt},
                                    ],
                                }
                            ],
                        }
                    )

                    response = (
                        analyzer.bedrock_runtime.invoke_model_with_response_stream(
                            modelId=analyzer.model, body=body
                        )
                    )

                    for event in response.get("body"):
                        if "chunk" in event:
                            chunk = json.loads(event["chunk"]["bytes"])
                            if (
                                "type" in chunk
                                and chunk["type"] == "content_block_delta"
                                and "delta" in chunk
                            ):
                                text = chunk["delta"].get("text", "")
                                if text:
                                    result_text += text
                                    yield f"data: {json.dumps({'text': text})}\n\n"

                # 完了通知
                yield f"data: {json.dumps({'complete': True})}\n\n"

            except Exception as e:
                print(f"ストリーミングエラー: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                # 一時ファイルを削除
                try:
                    os.remove(temp_path)
                except:
                    pass

        # レスポンスの作成とリターン
        return Response(generate(), mimetype="text/event-stream")

    except Exception as e:
        print(f"API全体エラー: {str(e)}")
        # 一時ファイルを削除
        try:
            os.remove(temp_path)
        except:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/static/<path:path>")
def serve_static(path):
    """静的ファイルを提供"""
    return send_from_directory("static", path)


if __name__ == "__main__":
    # クエリ文字列で直接実行をサポート
    print("Claude3 Video Analyzer Webサーバーを起動します...")
    print(f"使用モデル: {analyzer.model}")
    print(f"使用モード: {'Bedrock' if analyzer.use_bedrock else 'Anthropic Direct'}")
    print("http://localhost:5000/ にアクセスしてください")

    app.run(debug=True, host="0.0.0.0", port=5000)
