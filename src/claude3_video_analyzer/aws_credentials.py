#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS認証情報の自動リフレッシュと検証のためのモジュール
セキュリティトークンが無効になった場合の自動リカバリを提供
"""

import os
import logging
import boto3
import botocore.exceptions
import time
from functools import wraps

logger = logging.getLogger(__name__)

class CredentialManager:
    """AWS認証情報を管理し、無効なトークンを自動的にリフレッシュするクラス"""
    
    def __init__(self, region_name=None):
        """
        認証情報マネージャーを初期化
        
        Args:
            region_name: 使用するAWSリージョン（Noneの場合は環境変数から取得）
        """
        self.region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self.session = None
        self.last_refresh_time = 0
        self.refresh_interval = 3600  # 1時間ごとに自動リフレッシュ
        self.refresh_credentials()
    
    def refresh_credentials(self):
        """AWS認証情報を更新する"""
        logger.info("AWS認証情報をリフレッシュしています...")
        
        try:
            # 環境変数からの認証情報チェック
            aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
            
            if aws_access_key and aws_secret_key:
                # 環境変数の認証情報を使用
                logger.info("環境変数からAWS認証情報を使用します")
                self.session = boto3.Session(
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key,
                    region_name=self.region_name
                )
            else:
                # デフォルトの認証情報チェーンを使用
                logger.info("AWSデフォルト認証情報チェーンを使用します")
                self.session = boto3.Session(region_name=self.region_name)
                
            # 認証情報が有効かどうかをテスト
            sts = self.session.client('sts')
            identity = sts.get_caller_identity()
            logger.info(f"AWS認証情報の検証成功: {identity.get('Arn')}")
            
            # リフレッシュ時間を更新
            self.last_refresh_time = time.time()
            return True
            
        except Exception as e:
            logger.error(f"AWS認証情報のリフレッシュに失敗しました: {str(e)}")
            return False
    
    def check_credentials(self, force_refresh=False):
        """
        認証情報の有効性をチェックし、必要に応じてリフレッシュする
        
        Args:
            force_refresh: 強制的にリフレッシュするかどうか
            
        Returns:
            bool: 認証情報が有効かどうか
        """
        current_time = time.time()
        
        # 強制リフレッシュまたは一定時間経過で認証情報をリフレッシュ
        if force_refresh or (current_time - self.last_refresh_time > self.refresh_interval):
            return self.refresh_credentials()
            
        return True
    
    def get_client(self, service_name, config=None):
        """
        特定のAWSサービスのクライアントを取得する。
        認証情報が無効な場合は自動的にリフレッシュを試みる。

        Args:
            service_name: AWS サービス名 ('bedrock-runtime' など)
            config: botocore の設定オブジェクト

        Returns:
            boto3クライアント
        """
        # 認証情報をチェック
        self.check_credentials()

        if not self.session:
            logger.error("有効なAWSセッションがありません")
            raise RuntimeError("AWS認証情報の取得に失敗しました")

        try:
            return self.session.client(service_name=service_name, config=config)
        except Exception as e:
            logger.error(f"{service_name}クライアントの作成に失敗: {str(e)}")
            # エラーが発生した場合、認証情報をリフレッシュして再試行
            if self.refresh_credentials():
                try:
                    return self.session.client(service_name=service_name, config=config)
                except Exception as retry_e:
                    logger.error(f"リフレッシュ後も{service_name}クライアント作成に失敗: {str(retry_e)}")
                    raise
            else:
                raise

    def get_resource(self, service_name, config=None):
        """
        特定のAWSサービスのリソースを取得する。
        認証情報が無効な場合は自動的にリフレッシュを試みる。

        Args:
            service_name: AWS サービス名 ('dynamodb' など)
            config: botocore の設定オブジェクト

        Returns:
            boto3リソース
        """
        # 認証情報をチェック
        self.check_credentials()

        if not self.session:
            logger.error("有効なAWSセッションがありません")
            raise RuntimeError("AWS認証情報の取得に失敗しました")

        try:
            return self.session.resource(service_name=service_name, config=config)
        except Exception as e:
            logger.error(f"{service_name}リソースの作成に失敗: {str(e)}")
            # エラーが発生した場合、認証情報をリフレッシュして再試行
            if self.refresh_credentials():
                try:
                    return self.session.resource(service_name=service_name, config=config)
                except Exception as retry_e:
                    logger.error(f"リフレッシュ後も{service_name}リソース作成に失敗: {str(retry_e)}")
                    raise
            else:
                raise

def with_aws_credential_refresh(func):
    """
    AWS API呼び出しのための認証情報リフレッシュデコレーター
    UnrecognizedClientExceptionが発生した場合に認証情報を自動的にリフレッシュ
    
    Args:
        func: デコレートする関数
    
    Returns:
        デコレートされた関数
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', '')
            
            # 認証エラーを検出
            if error_code in ('UnrecognizedClientException', 'InvalidSignatureException', 
                             'ExpiredTokenException', 'InvalidClientTokenId') or \
               'security token' in error_message.lower() or \
               'invalid' in error_message.lower() and 'token' in error_message.lower():
                logger.warning(f"AWS認証エラーを検出: {error_code} - {error_message}")
                
                # 認証情報を強制的にリフレッシュ
                logger.info("認証情報をリフレッシュして再試行します...")
                if hasattr(self, 'credential_manager') and self.credential_manager:
                    # すでに認証情報マネージャーがある場合
                    self.credential_manager.refresh_credentials()
                else:
                    # 認証情報マネージャーを新規作成
                    self.credential_manager = CredentialManager()
                
                # BedrockクライアントとAgentクライアントを再作成
                region_name = getattr(self, 'region_name', 'us-east-1')
                
                # サービスクライアントを再作成
                # bedrock-runtime クライアントの更新
                if hasattr(self, 'bedrock_runtime'):
                    try:
                        import botocore
                        client_config = botocore.config.Config(
                            connect_timeout=30,
                            read_timeout=120,
                            retries={'max_attempts': 5, 'mode': 'adaptive'},
                            max_pool_connections=20,
                            tcp_keepalive=True
                        )
                        self.bedrock_runtime = self.credential_manager.get_client(
                            'bedrock-runtime', config=client_config
                        )
                        logger.info("bedrock-runtimeクライアントを再作成しました")
                    except Exception as rebuild_e:
                        logger.error(f"bedrock-runtimeクライアント再作成エラー: {str(rebuild_e)}")
                
                # bedrock-agent-runtime クライアントの更新
                if hasattr(self, 'bedrock_agent_client'):
                    try:
                        import botocore
                        agent_config = botocore.config.Config(
                            connect_timeout=30,
                            read_timeout=120,
                            retries={'max_attempts': 5, 'mode': 'adaptive'},
                            max_pool_connections=20,
                            tcp_keepalive=True
                        )
                        self.bedrock_agent_client = self.credential_manager.get_client(
                            'bedrock-agent-runtime', config=agent_config
                        )
                        logger.info("bedrock-agent-runtimeクライアントを再作成しました")
                    except Exception as rebuild_agent_e:
                        logger.error(f"bedrock-agent-runtimeクライアント再作成エラー: {str(rebuild_agent_e)}")
                
                # 再試行
                try:
                    logger.info("認証情報リフレッシュ後に呼び出しを再試行します")
                    return func(self, *args, **kwargs)
                except Exception as retry_e:
                    logger.error(f"認証情報リフレッシュ後も呼び出しに失敗しました: {str(retry_e)}")
                    # 再試行しても失敗する場合は、ユーザーにわかりやすいエラーメッセージを示す
                    if isinstance(retry_e, botocore.exceptions.ClientError):
                        error_code = retry_e.response.get('Error', {}).get('Code', '')
                        error_message = retry_e.response.get('Error', {}).get('Message', '')
                        if error_code in ('UnrecognizedClientException', 'InvalidSignatureException'):
                            raise ConnectionError(
                                "AWS認証情報が無効です。AWS_ACCESS_KEY_ID、AWS_SECRET_ACCESS_KEY、"
                                "および AWS_SESSION_TOKEN（使用している場合）環境変数が適切に設定され、"
                                "有効であることを確認してください。詳細: " + error_message
                            ) from retry_e
                    raise
            else:
                # 認証エラー以外はそのまま再スロー
                raise
        except Exception as e:
            # 認証情報リフレッシュが必要な例外かどうかをテキスト内容から判断
            error_text = str(e).lower()
            if ('security token' in error_text and 'invalid' in error_text) or \
               'unrecognized client' in error_text or \
               'expired token' in error_text:
                logger.warning(f"エラーメッセージから認証問題を検出: {str(e)}")
                
                # 認証情報を強制的にリフレッシュ
                logger.info("認証情報をリフレッシュして再試行します...")
                if hasattr(self, 'credential_manager') and self.credential_manager:
                    self.credential_manager.refresh_credentials()
                else:
                    self.credential_manager = CredentialManager()
                
                # BedrockクライアントとAgentクライアントを再作成
                if hasattr(self, 'bedrock_runtime'):
                    try:
                        import botocore
                        client_config = botocore.config.Config(
                            connect_timeout=30,
                            read_timeout=120,
                            retries={'max_attempts': 5, 'mode': 'adaptive'},
                            max_pool_connections=20,
                            tcp_keepalive=True
                        )
                        self.bedrock_runtime = self.credential_manager.get_client(
                            'bedrock-runtime', config=client_config
                        )
                    except Exception as rebuild_e:
                        logger.error(f"bedrockクライアント再作成エラー: {str(rebuild_e)}")
                
                # 再試行
                try:
                    return func(self, *args, **kwargs)
                except Exception as retry_e:
                    logger.error(f"認証情報リフレッシュ後も呼び出しに失敗しました: {str(retry_e)}")
                    raise ConnectionError(
                        "AWS認証エラー: 認証情報のリフレッシュを行いましたが、"
                        "APIコールは依然として失敗しています。IAM権限と認証情報を確認してください。"
                    ) from retry_e
            else:
                # その他のエラーはそのまま再スロー
                raise
    return wrapper