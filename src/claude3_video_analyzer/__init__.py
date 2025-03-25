import anthropic
import base64
import cv2
import os
import boto3
import json
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()

class VideoAnalyzer:
    def __init__(self):
        # モードを取得
        self.mode = os.getenv("MODE", "anthropic")  # デフォルトはAnthropicクライアント
        self.use_bedrock = False

        # Anthropicクライアント用の設定
        if self.mode == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key is None:
                raise ValueError("ANTHROPIC_API_KEY not found in environment variables or .env file.")
            
            # Anthropicクライアントの初期化
            self.client = anthropic.Anthropic(api_key=api_key)
            
        # AWS Bedrockクライアント用の設定
        elif self.mode == "bedrock":
            aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
            aws_region = os.getenv("AWS_REGION", "us-east-1")
            
            if aws_access_key is None or aws_secret_key is None:
                raise ValueError("AWS credentials not found in environment variables or .env file.")
                
            # Bedrockクライアントの初期化
            try:
                self.bedrock_runtime = boto3.client(
                    service_name="bedrock-runtime",
                    region_name=aws_region,
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key
                )
                self.use_bedrock = True
            except Exception as e:
                raise ConnectionError(f"Bedrockクライアントの初期化エラー: {str(e)}")
        else:
            raise ValueError(f"Unsupported mode '{self.mode}'. Use 'anthropic' or 'bedrock'.")

        # デフォルトの設定
        if self.use_bedrock:
            self.model = "anthropic.claude-3-5-sonnet-20240620-v1:0"  # オンデマンド対応のモデル
        else:
            self.model = "claude-3-sonnet-20240229"  # モデルを指定 "claude-3-opus-20240229" or "claude-3-sonnet-20240229"
        
        # 環境変数でモデルを上書き可能に
        self.model = os.getenv("MODEL_ID", self.model)
        
        # デフォルトプロンプト
        self.default_prompt = "これは動画のフレーム画像です。動画の最初から最後の流れ、動作を微分して日本語で解説してください。"

    def get_frames_from_video(self, file_path, max_images=20):
        """ビデオからフレームを抽出してbase64にエンコード"""
        video = cv2.VideoCapture(file_path)
        if not video.isOpened():
            raise FileNotFoundError(f"ビデオファイル '{file_path}' を開けませんでした。")
                
        base64_frames = []
        buffer = None
        
        while video.isOpened():
            success, frame = video.read()
            if not success:
                break
            _, buffer = cv2.imencode(".jpg", frame)
            base64_frame = base64.b64encode(buffer).decode("utf-8")
            base64_frames.append(base64_frame)
        video.release()
        
        # フレームがない場合はエラー
        if not base64_frames:
            raise ValueError("ビデオからフレームを抽出できませんでした。")
            
        # 選択する画像の数を制限する
        num_frames = len(base64_frames)
        if num_frames <= max_images:
            # フレーム数がmax_images以下の場合はすべて使用
            return base64_frames, buffer
        else:
            # フレーム数が多い場合は均等に抽出
            step = max(num_frames // max_images, 1)  # ゼロ除算を避ける
            selected_frames = base64_frames[0::step][:max_images]
            return selected_frames, buffer

    def analyze_video(self, file_path, prompt=None, model=None, max_images=20, stream_callback=None):
        """ビデオを解析してテキスト結果を返す"""
        # パラメータの設定
        if prompt is None:
            prompt = self.default_prompt
        
        if model is None:
            model = self.model

        # ビデオからフレームを取得
        base64_frames, _ = self.get_frames_from_video(file_path, max_images)
        
        # 結果を保存する変数
        result_text = ""
        
        # Anthropic APIかBedrock APIかによって処理を分岐
        if not self.use_bedrock:
            # Claude APIにリクエストを送信（Anthropicクライアント）
            with self.client.messages.stream(
                model=model,  # モデル指定
                max_tokens=1024,  # 最大トークン数
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
                    result_text += text
                    if stream_callback:
                        stream_callback(text)
        else:
            # Bedrock APIにリクエストを送信
            # Bedrockのリクエストボディを作成
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
            
            try:
                # Bedrockにストリーミングリクエストを送信
                response = self.bedrock_runtime.invoke_model_with_response_stream(
                    modelId=model,
                    body=body
                )
                
                # レスポンスストリームを処理
                for event in response.get("body"):
                    if "chunk" in event:
                        chunk = json.loads(event["chunk"]["bytes"])
                        if "type" in chunk and chunk["type"] == "content_block_delta" and "delta" in chunk:
                            text = chunk["delta"].get("text", "")
                            if text:
                                result_text += text
                                if stream_callback:
                                    stream_callback(text)
            except Exception as e:
                raise RuntimeError(f"Bedrock API error: {str(e)}")
                
        return result_text