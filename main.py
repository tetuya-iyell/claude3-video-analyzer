import os
import time
import tempfile
import json
import logging
import uuid
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
from src.claude3_video_analyzer import VideoAnalyzer, ScriptGenerator
from src.claude3_video_analyzer.api_routes import create_bedrock_scripts_blueprint
from goose_lib.api import goose_bp

# ロギング設定
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# ロガーインスタンスの作成
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
# セッションの設定
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())

# Note: flask-sessionライブラリを使用しない場合の代替設定
# 代わりに標準のFlaskセッションを使用するが、大きなセッションデータを扱うために
# データをファイルシステムに保存する独自の仕組みを実装する
app.config['SESSION_TYPE'] = 'cookie'      # クッキーベースのセッション(デフォルト)
app.config['SESSION_PERMANENT'] = True     # 永続的セッション
app.config['SESSION_USE_SIGNER'] = True    # セッションクッキーの署名

# セッションデータ保存用のディレクトリ
SESSION_DATA_DIR = os.path.join(os.getcwd(), "flask_sessions")
if not os.path.exists(SESSION_DATA_DIR):
    os.makedirs(SESSION_DATA_DIR)

CORS(app)

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

    # ScriptGeneratorインスタンスの作成
    script_generator = ScriptGenerator(analyzer)
    print("台本生成エンジンの初期化が完了しました")

    # DynamoDB設定のログ出力
    dynamodb_enabled = os.environ.get('DYNAMODB_ENABLED', 'false').lower() in ('true', 'yes', '1')
    if dynamodb_enabled:
        print("DynamoDB統合が有効です")
        scripts_table = os.environ.get('DYNAMODB_SCRIPTS_TABLE', 'YukkuriScripts')
        merged_scripts_table = os.environ.get('DYNAMODB_MERGED_SCRIPTS_TABLE', 'YukkuriMergedScripts')
        print(f"使用テーブル: Scripts={scripts_table}, MergedScripts={merged_scripts_table}")
    else:
        print("DynamoDB統合は無効です（使用するには DYNAMODB_ENABLED=true を設定）")

    # Bedrockスクリプトジェネレーター用のBlueprintを作成して登録
    bedrock_scripts_bp = create_bedrock_scripts_blueprint(script_generator)
    app.register_blueprint(bedrock_scripts_bp)
    print("Bedrock台本生成APIルートを登録しました")
except Exception as e:
    print(f"初期化エラー: {str(e)}")
    raise

# Goose API Blueprintを登録
app.register_blueprint(goose_bp)
print("Goose台本生成APIルートを登録しました")


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

                        # ストリーミングAPIが拒否されているため、通常の同期APIを使用
                        logger.info("ストリーミングAPIが利用できないため、通常のAPIを使用します")
                        
                        # Claude 3.5 Sonnetモデル用のリクエスト形式（仕様通り）
                        try:
                            # Anthropicモデル用（標準）
                            response = analyzer.bedrock_runtime.invoke_model(
                                modelId=analyzer.model, body=body
                            )
                        except Exception as e:
                            error_msg = str(e)
                            if "AccessDeniedException" in error_msg and "is not authorized to perform" in error_msg:
                                # IAM権限エラーの場合、詳細なエラーメッセージを提供
                                logger.error("AWS IAM権限エラー: Bedrock APIへのアクセス権限がありません")
                                logger.error("必要な権限: bedrock:InvokeModel")
                                logger.error("AWS管理者に以下の権限を要求してください:")
                                logger.error("1. AWS IAM コンソールでユーザーのポリシーを確認")
                                logger.error("2. Bedrock APIへのアクセス権限を追加 (bedrock:InvokeModel)")
                                logger.error("3. 特に anthropic.claude-3-5-sonnet-20240620-v1:0 へのアクセスを確保")
                                yield f"data: {json.dumps({'error': 'AWS Bedrock APIアクセス権限エラー: このアプリケーションはAWS IAM権限の設定が必要です'})}\n\n"
                                return
                            else:
                                # その他のエラーはそのまま伝播
                                raise
                        
                        # 応答本体から結果を抽出
                        response_body = json.loads(response.get('body').read())
                        
                        # Claudeモデル専用の応答処理（仕様に従って）
                        if 'content' in response_body and len(response_body['content']) > 0:
                            for content_item in response_body['content']:
                                if content_item.get('type') == 'text':
                                    text = content_item.get('text', '')
                                    
                                    # テキストを小さな部分に分割して疑似ストリーミング
                                    chunk_size = 20  # 20文字ずつ送信
                                    for i in range(0, len(text), chunk_size):
                                        text_chunk = text[i:i+chunk_size]
                                        yield f"data: {json.dumps({'text': text_chunk})}\n\n"
                                        import time
                                        time.sleep(0.05)  # 少し待機して疑似ストリーミング

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

                    # ストリーミングAPIが拒否されているため、通常の同期APIを使用
                    logger.info("ストリーミングAPIが利用できないため、通常のAPIを使用します")
                    
                    # Claude 3.5 Sonnetモデル用のリクエスト形式（仕様通り）
                    try:
                        # Anthropicモデル用（標準）
                        response = analyzer.bedrock_runtime.invoke_model(
                            modelId=analyzer.model, body=body
                        )
                    except Exception as e:
                        error_msg = str(e)
                        if "AccessDeniedException" in error_msg and "is not authorized to perform" in error_msg:
                            # IAM権限エラーの場合、詳細なエラーメッセージを提供
                            logger.error("AWS IAM権限エラー: Bedrock APIへのアクセス権限がありません")
                            logger.error("必要な権限: bedrock:InvokeModel")
                            logger.error("AWS管理者に以下の権限を要求してください:")
                            logger.error("1. AWS IAM コンソールでユーザーのポリシーを確認")
                            logger.error("2. Bedrock APIへのアクセス権限を追加 (bedrock:InvokeModel)")
                            logger.error("3. 特に anthropic.claude-3-5-sonnet-20240620-v1:0 へのアクセスを確保")
                            yield f"data: {json.dumps({'error': 'AWS Bedrock APIアクセス権限エラー: このアプリケーションはAWS IAM権限の設定が必要です'})}\n\n"
                            return
                        else:
                            # その他のエラーはそのまま伝播
                            raise
                    
                    # 応答本体から結果を抽出
                    response_body = json.loads(response.get('body').read())
                    result_text = ""
                    
                    # Claudeモデル専用の応答処理（仕様に従って）
                    if 'content' in response_body and len(response_body['content']) > 0:
                        for content_item in response_body['content']:
                            if content_item.get('type') == 'text':
                                text = content_item.get('text', '')
                                result_text += text
                                
                                # テキストを小さな部分に分割して疑似ストリーミング
                                chunk_size = 20  # 20文字ずつ送信
                                for i in range(0, len(text), chunk_size):
                                    text_chunk = text[i:i+chunk_size]
                                    yield f"data: {json.dumps({'text': text_chunk})}\n\n"
                                    import time
                                    time.sleep(0.05)  # 少し待機して疑似ストリーミング

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


# 台本生成API
@app.route("/api/bedrock-scripts/analyze-chapters", methods=["POST"])
def bedrock_analyze_chapters():
    """章立て解析結果から各章を抽出するAPI（Bedrock版）"""
    data = request.json
    if not data or 'analysis_text' not in data:
        return jsonify({"error": "解析テキストが提供されていません"}), 400
        
    analysis_text = data['analysis_text']
    
    try:
        # 章の抽出
        chapters = script_generator.extract_chapters(analysis_text)
        
        # セッションIDを生成（なければ）
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        
        session_id = session['session_id']
        
        # ファイルにチャプターデータを保存
        chapters_file = os.path.join(SESSION_DATA_DIR, f"{session_id}_chapters.json")
        with open(chapters_file, 'w', encoding='utf-8') as f:
            json.dump(chapters, f, ensure_ascii=False)
        
        # セッションには参照のみ保存
        session['chapters_file'] = chapters_file
        
        return jsonify({
            "success": True,
            "chapters": chapters
        })
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"章構造抽出エラー: {str(e)}")
        print(f"トレースバック: {error_traceback}")
        return jsonify({"error": f"章構造の抽出に失敗しました: {str(e)}"}), 500


@app.route("/api/bedrock-scripts/generate-script", methods=["POST"])
def bedrock_generate_script():
    """特定の章の台本を生成するAPI（Bedrock版）"""
    data = request.json
    if not data or 'chapter_index' not in data:
        return jsonify({"error": "章のインデックスが指定されていません"}), 400
        
    chapter_index = int(data['chapter_index'])
    
    # セッションIDの確認
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']
    
    # 章情報の取得
    chapters = data.get('chapters')
    if not chapters:
        # セッションから章情報のファイルパスを取得
        chapters_file = session.get('chapters_file')
        if chapters_file and os.path.exists(chapters_file):
            with open(chapters_file, 'r', encoding='utf-8') as f:
                chapters = json.load(f)
        else:
            return jsonify({"error": "章情報が見つかりません"}), 404
    else:
        # クライアントから送信された章情報をファイルに保存
        chapters_file = os.path.join(SESSION_DATA_DIR, f"{session_id}_chapters.json")
        with open(chapters_file, 'w', encoding='utf-8') as f:
            json.dump(chapters, f, ensure_ascii=False)
        session['chapters_file'] = chapters_file
        logging.info(f"クライアントから送信された章情報をファイルに保存しました: {len(chapters)}章")
    
    if not chapters or chapter_index >= len(chapters):
        return jsonify({"error": "指定された章が見つかりません"}), 404
        
    chapter = chapters[chapter_index]
    
    try:
        # 動画時間パラメータを取得（設定されていなければデフォルト3分）
        duration_minutes = int(data.get('duration_minutes', 3))
        
        # 台本生成（動画時間パラメータを渡す）
        script_data = script_generator.generate_script_for_chapter(chapter, duration_minutes)
        
        # スクリプト情報のファイル
        scripts_file = os.path.join(SESSION_DATA_DIR, f"{session_id}_scripts.json")
        
        # 既存のスクリプトを読み込む
        scripts = []
        if os.path.exists(scripts_file):
            with open(scripts_file, 'r', encoding='utf-8') as f:
                scripts = json.load(f)
        
        logging.info(f"現在のスクリプト数: {len(scripts)}")
        
        # スクリプト配列を必要に応じて拡張
        while len(scripts) <= chapter_index:
            scripts.append(None)
            
        # 台本を保存
        scripts[chapter_index] = script_data
        
        # ファイルに保存
        with open(scripts_file, 'w', encoding='utf-8') as f:
            json.dump(scripts, f, ensure_ascii=False)
            
        # セッションに参照を保存
        session['scripts_file'] = scripts_file
        
        logging.info(f"台本をファイルに保存しました。chapter_index: {chapter_index}, スクリプト総数: {len(scripts)}")
        
        return jsonify({
            "success": True,
            "script": script_data,
            "chapter_index": chapter_index
        })
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"台本生成エラー: {str(e)}")
        print(f"トレースバック: {error_traceback}")
        return jsonify({"error": f"台本生成に失敗しました: {str(e)}"}), 500


@app.route("/api/bedrock-scripts/analyze-script", methods=["POST"])
def bedrock_analyze_script():
    """台本の品質を分析するAPI（Bedrock版）"""
    data = request.json
    if not data or 'chapter_index' not in data:
        return jsonify({"error": "章のインデックスが指定されていません"}), 400
        
    chapter_index = data['chapter_index']
    script_content = data.get('script_content')
    # 動画時間パラメータを取得（設定されていなければデフォルト3分）
    duration_minutes = int(data.get('duration_minutes', 3))
    
    # 台本の取得
    scripts = session.get('scripts', [])
    if chapter_index >= len(scripts):
        return jsonify({"error": "指定された章の台本が見つかりません"}), 404
    
    script_data = scripts[chapter_index]
    
    # script_contentが指定された場合は、台本内容を更新
    if script_content:
        script_data['script_content'] = script_content
        scripts[chapter_index] = script_data
        session['scripts'] = scripts
    
    try:
        # 品質分析
        analysis_result = script_generator.analyze_script_quality(script_data)
        
        # 分析結果を保存
        script_data['analysis'] = analysis_result['analysis']
        script_data['passed'] = analysis_result['passed']
        # 動画時間パラメータを保存
        script_data['duration_minutes'] = duration_minutes
        logging.info(f"台本に動画時間を保存: {duration_minutes}分")
        
        scripts[chapter_index] = script_data
        session['scripts'] = scripts
        
        return jsonify({
            "success": True,
            "passed": analysis_result['passed'],
            "analysis": analysis_result['analysis'],
            "chapter_index": chapter_index
        })
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"台本分析エラー: {str(e)}")
        print(f"トレースバック: {error_traceback}")
        return jsonify({"error": f"台本分析に失敗しました: {str(e)}"}), 500


@app.route("/api/bedrock-scripts/submit-feedback", methods=["POST"])
def bedrock_submit_feedback():
    """台本にフィードバックを送信するAPI（Bedrock版）"""
    data = request.json
    if not data or 'chapter_index' not in data or 'feedback' not in data or 'is_approved' not in data:
        return jsonify({"error": "必須パラメータが不足しています"}), 400
    
    chapter_index = int(data['chapter_index'])  # 明示的に整数型に変換
    feedback_text = data['feedback']
    is_approved = data['is_approved']
    
    # 動画時間パラメータを取得（設定されていなければデフォルト3分）
    duration_minutes = int(data.get('duration_minutes', 3))
    
    logging.info(f"フィードバック受信: chapter_index={chapter_index}, is_approved={is_approved}, feedback長さ={len(feedback_text)}, duration_minutes={duration_minutes}")
    
    # セッションIDの確認
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']
    
    # スクリプトデータをファイルから取得
    scripts_file = session.get('scripts_file')
    if not scripts_file or not os.path.exists(scripts_file):
        logging.error("スクリプトファイルが見つかりません")
        return jsonify({"error": "スクリプトデータが見つかりません"}), 404
    
    # スクリプトデータを読み込む
    with open(scripts_file, 'r', encoding='utf-8') as f:
        scripts = json.load(f)
    
    logging.info(f"ファイルから読み込んだスクリプト数: {len(scripts)}")
    
    # スクリプト配列を必要に応じて拡張
    while len(scripts) <= chapter_index:
        scripts.append(None)
        logging.info(f"スクリプト配列を拡張: 新しいサイズ={len(scripts)}")
    
    # スクリプトデータが存在しない場合のエラーチェック
    if scripts[chapter_index] is None:
        return jsonify({"error": f"章 {chapter_index} の台本データが見つかりません"}), 404
    
    script_data = scripts[chapter_index]
    
    try:
        # フィードバックの処理
        if is_approved:
            # 承認の場合
            script_data['status'] = "approved"
            logging.info(f"台本を承認しました: chapter_index={chapter_index}")
        else:
            # フィードバックの場合
            script_data['status'] = "rejected"
            if 'feedback' not in script_data:
                script_data['feedback'] = []
            script_data['feedback'].append(feedback_text)
            logging.info(f"フィードバックを追加: chapter_index={chapter_index}, フィードバック数={len(script_data['feedback'])}")
            
            # 詳細なログ:改善前の状態
            logging.info(f"台本改善前の状態:")
            logging.info(f"  chapter_index: {chapter_index}")
            logging.info(f"  status: {script_data['status']}")
            logging.info(f"  script_content文字数: {len(script_data['script_content'])}")
            logging.info(f"  'improved_script'キー: {'存在する' if 'improved_script' in script_data else '存在しない'}")
            if 'improved_script' in script_data:
                logging.info(f"  既存のimproved_script文字数: {len(script_data['improved_script'])}")
                # 次の改善リクエストで問題になるかもしれないので削除しておく
                del script_data['improved_script']
                logging.info(f"  既存のimproved_scriptを削除しました")
            
            # フィードバックに基づいて台本を改善
            logging.info(f"台本改善処理を開始: フィードバック長さ={len(feedback_text)}")
            
            # 台本改善時に動画時間パラメータを渡すための処理
            # スクリプトデータに動画時間を設定（改善関数内で使用可能にする）
            script_data['duration_minutes'] = duration_minutes
            
            improved_script_data = script_generator.improve_script(script_data, feedback_text)
            logging.info(f"台本改善処理が完了: 結果タイプ={type(improved_script_data)}")
            
            # 明示的に improved_script キーを設定
            # 改善されたスクリプトが辞書型か文字列型かを確認
            logging.info(f"改善スクリプトデータの詳細処理:")
            logging.info(f"  データ型: {type(improved_script_data)}")
            if isinstance(improved_script_data, dict):
                logging.info(f"  辞書型の場合のキー: {list(improved_script_data.keys())}")
                
            # 目標文字数を計算
            expected_chars = script_generator.calculate_expected_length(duration_minutes)
            
            if isinstance(improved_script_data, dict) and 'script_content' in improved_script_data:
                # 辞書型の場合は script_content キーを使用
                script_content = improved_script_data['script_content']
                actual_chars = len(script_content)
                logging.info(f"台本の改善が完了しました（辞書型）。長さ={actual_chars}")
                
                # 文字数チェック - 目標文字数に達していない場合は自動補完
                if actual_chars < expected_chars:
                    logging.info(f"文字数不足のため補完処理を開始: 現在={actual_chars}, 目標={expected_chars}")
                    script_content = script_generator.ensure_minimum_length(script_content, expected_chars, script_data)
                    actual_chars = len(script_content)
                    logging.info(f"補完処理後の文字数: {actual_chars}文字")
                
                # 処理済みのcontent_scriptを設定（最終サニタイズ処理を適用）
                from src.claude3_video_analyzer import sanitize_script
                sanitized_content = sanitize_script(script_content)
                script_data['improved_script'] = sanitized_content
                logging.info(f"台本の改善と補完が完了しました。最終サニタイズ適用済み。最終長さ={len(script_data['improved_script'])}")
                
                # 最終的な文字数チェックとログ出力
                actual_chars = len(script_data['improved_script'])
                logging.info(f"改善台本の文字数チェック: 実際={actual_chars}文字, 期待={expected_chars}文字")
                if actual_chars < expected_chars:
                    logging.warning(f"全ての処理後も目標文字数に達していません: 目標={expected_chars}, 実際={actual_chars}")
                else:
                    logging.info(f"目標文字数を達成しました: 目標={expected_chars}, 実際={actual_chars}")
                
            elif isinstance(improved_script_data, str):
                # 文字列型の場合はそのまま使用
                script_content = improved_script_data
                actual_chars = len(script_content)
                logging.info(f"台本の改善が完了しました（文字列型）。長さ={actual_chars}")
                
                # 文字数チェック - 目標文字数に達していない場合は自動補完
                if actual_chars < expected_chars:
                    logging.info(f"文字数不足のため補完処理を開始: 現在={actual_chars}, 目標={expected_chars}")
                    script_content = script_generator.ensure_minimum_length(script_content, expected_chars, script_data)
                    actual_chars = len(script_content)
                    logging.info(f"補完処理後の文字数: {actual_chars}文字")
                
                # 処理済みのcontent_scriptを設定（最終サニタイズ処理を適用）
                from src.claude3_video_analyzer import sanitize_script
                sanitized_content = sanitize_script(script_content)
                script_data['improved_script'] = sanitized_content
                logging.info(f"台本の改善と補完が完了しました。最終サニタイズ適用済み。最終長さ={len(script_data['improved_script'])}")
                
                # 最終的な文字数チェックとログ出力
                actual_chars = len(script_data['improved_script'])
                logging.info(f"改善台本の文字数チェック: 実際={actual_chars}文字, 期待={expected_chars}文字")
                if actual_chars < expected_chars:
                    logging.warning(f"全ての処理後も目標文字数に達していません: 目標={expected_chars}, 実際={actual_chars}")
                else:
                    logging.info(f"目標文字数を達成しました: 目標={expected_chars}, 実際={actual_chars}")
                
            else:
                # それ以外の型の場合はエラーログを出力
                logging.error(f"台本の改善に失敗: 予期しないデータ型 {type(improved_script_data)}")
                logging.error(f"改善結果のダンプ: {str(improved_script_data)[:200]}...")
                # エラー対策としてスクリプトの内容をそのままコピー
                script_data['improved_script'] = script_data['script_content']
                script_data['improved_script'] += "\n\n（フィードバックによる改善に失敗しました。手動で編集してください）"
                logging.info(f"エラー時のフォールバック台本を設定しました。長さ={len(script_data['improved_script'])}")
        
        # 変更を保存
        scripts[chapter_index] = script_data
        
        # 変更内容のより詳細なログ出力
        logging.info(f"台本の更新内容: chapter_index={chapter_index}, status={script_data['status']}")
        if 'improved_script' in script_data:
            logging.info(f"台本の改善データあり: 文字数={len(script_data['improved_script'])}")
        else:
            logging.info(f"台本の改善データなし")
            
        if 'feedback' in script_data:
            logging.info(f"台本のフィードバック: {len(script_data['feedback'])}件")
        
        # ファイルに保存
        with open(scripts_file, 'w', encoding='utf-8') as f:
            json.dump(scripts, f, ensure_ascii=False)
        
        logging.info(f"台本をファイルに保存: chapter_index={chapter_index}, スクリプト総数={len(scripts)}")
        
        return jsonify({
            "success": True,
            "chapter_index": chapter_index,
            "is_approved": is_approved,
            "improved_script": script_data.get('improved_script', None) if not is_approved else None
        })
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"フィードバック処理エラー: {str(e)}")
        print(f"トレースバック: {error_traceback}")
        return jsonify({"error": f"フィードバック処理に失敗しました: {str(e)}"}), 500


@app.route("/api/bedrock-scripts/apply-improvement", methods=["POST"])
def bedrock_apply_improvement():
    """改善された台本を適用するAPI（Bedrock版）"""
    data = request.json
    if not data or 'chapter_index' not in data:
        return jsonify({"error": "章のインデックスが指定されていません"}), 400
        
    chapter_index = int(data['chapter_index'])  # 明示的に整数型に変換
    # 動画時間パラメータを取得（設定されていなければデフォルト3分）
    duration_minutes = int(data.get('duration_minutes', 3))
    
    # セッションIDの確認
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']
    
    # スクリプトデータをファイルから取得
    scripts_file = session.get('scripts_file')
    if not scripts_file or not os.path.exists(scripts_file):
        logging.error("スクリプトファイルが見つかりません")
        return jsonify({"error": "スクリプトデータが見つかりません"}), 404
    
    # スクリプトデータを読み込む
    with open(scripts_file, 'r', encoding='utf-8') as f:
        scripts = json.load(f)
    
    logging.info(f"apply_improvement: ファイルから読み込んだスクリプト数: {len(scripts)}")
    
    if chapter_index >= len(scripts) or scripts[chapter_index] is None:
        logging.error(f"指定された章の台本が見つかりません。chapter_index: {chapter_index}, スクリプト数: {len(scripts)}")
        return jsonify({"error": "指定された章の台本が見つかりません"}), 404
    
    script_data = scripts[chapter_index]
    logging.info(f"台本データのキー: {list(script_data.keys())}")
    
    # improved_scriptキーが存在するか確認
    if 'improved_script' not in script_data or not script_data['improved_script']:
        logging.error(f"改善された台本が見つかりません。chapter_index: {chapter_index}, script_data keys: {list(script_data.keys())}")
        
        # 実験的に改善された台本が無い場合は元の台本をそのまま適用
        logging.info("改善された台本がないため、status を review に変更します")
        script_data['status'] = "review"
        scripts[chapter_index] = script_data
        
        # ファイルに保存
        with open(scripts_file, 'w', encoding='utf-8') as f:
            json.dump(scripts, f, ensure_ascii=False)
        
        return jsonify({
            "success": True,
            "chapter_index": chapter_index,
            "script": script_data,
            "warning": "改善された台本はありませんでしたが、ステータスを更新しました"
        })
    
    try:
        # 改善された台本を適用
        logging.info(f"改善された台本を適用します。長さ={len(script_data['improved_script'])}")
        script_data['script_content'] = script_data['improved_script']
        script_data['status'] = "completed"  # 「編集完了」ステータスに変更
        
        # 動画時間パラメータを保存
        script_data['duration_minutes'] = duration_minutes
        logging.info(f"台本に動画時間を保存: {duration_minutes}分")
        
        # 更新後は改善台本キーを削除
        del script_data['improved_script']
        
        # 安全のため、_original_contentも削除（フロントエンドで保存されている可能性がある）
        if '_original_content' in script_data:
            del script_data['_original_content']
            logging.info(f"台本更新後、_original_content キーを削除しました")
            
        logging.info(f"台本更新後、improved_script キーを削除しました")
        
        # 変更を保存
        scripts[chapter_index] = script_data
        
        # 詳細なデバッグ情報を出力
        logging.info(f"台本を改善版で更新します - 詳細状態:")
        logging.info(f"  chapter_index: {chapter_index}")
        logging.info(f"  更新後status: {script_data['status']}")
        logging.info(f"  script_content文字数: {len(script_data['script_content'])}")
        logging.info(f"  'improved_script'キーの削除: 完了")
        
        # ファイルに保存
        with open(scripts_file, 'w', encoding='utf-8') as f:
            json.dump(scripts, f, ensure_ascii=False)
            
        logging.info(f"台本を改善版で更新しました。chapter_index: {chapter_index}")
        
        return jsonify({
            "success": True,
            "chapter_index": chapter_index,
            "script": script_data
        })
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"台本改善適用エラー: {str(e)}")
        print(f"トレースバック: {error_traceback}")
        return jsonify({"error": f"台本改善の適用に失敗しました: {str(e)}"}), 500


@app.route("/api/bedrock-scripts/get-all-scripts", methods=["GET"])
def bedrock_get_all_scripts():
    """すべての台本を取得するAPI（Bedrock版）"""
    # セッションIDの確認
    if 'session_id' not in session:
        return jsonify({
            "success": True,
            "scripts": []
        })

    session_id = session['session_id']

    # スクリプトデータをファイルから取得
    scripts_file = session.get('scripts_file')
    if not scripts_file or not os.path.exists(scripts_file):
        return jsonify({
            "success": True,
            "scripts": []
        })

    # スクリプトデータを読み込む
    with open(scripts_file, 'r', encoding='utf-8') as f:
        scripts = json.load(f)

    logging.info(f"全スクリプト取得: {len(scripts)}件")

    return jsonify({
        "success": True,
        "scripts": scripts
    })


# DynamoDB同期エンドポイントは api_routes.py の Blueprint で定義済みのため削除
# API Blueprint経由で /api/bedrock-scripts/sync-with-dynamodb が既に実装されています


# エラーハンドリング
@app.errorhandler(500)
def internal_server_error(error):
    logging.error(f"500エラー: {error}")
    return jsonify({
        "error": "サーバー内部エラーが発生しました",
        "details": str(error)
    }), 500

if __name__ == "__main__":
    # クエリ文字列で直接実行をサポート
    print("Claude3 Video Analyzer Webサーバーを起動します...")
    print(f"使用モデル: {analyzer.model}")
    print(f"使用モード: {'Bedrock' if analyzer.use_bedrock else 'Anthropic Direct'}")

    # AI Agentの設定を表示（デバッグ用）
    if hasattr(analyzer, 'bedrock_agent_client') and analyzer.bedrock_agent_client is not None:
        print(f"Bedrock AI Agent: 有効 (Agent ID: {analyzer.bedrock_agent_id}, Alias ID: {analyzer.bedrock_agent_alias_id})")
    else:
        print(f"Bedrock AI Agent: 無効")

    print("http://localhost:5000/ にアクセスしてください")

    app.run(debug=True, host="0.0.0.0", port=5000)
