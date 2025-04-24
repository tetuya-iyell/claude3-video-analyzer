#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS APIリトライロジックのカスタム実装モジュール
バイナリデータ処理と例外処理を強化したもの
"""

import time
import logging
import random
import json
from functools import wraps

logger = logging.getLogger(__name__)

def aws_api_retry(max_retries=3, base_delay=1, jitter=0.3):
    """
    AWS APIへのコールのためのリトライデコレーター
    
    引数:
        max_retries (int): 最大リトライ回数
        base_delay (float): 基本待機時間（秒）
        jitter (float): ランダムなジッターの最大値（秒）
    
    用法:
        @aws_api_retry(max_retries=3)
        def call_aws_api():
            return aws_client.some_api_call()
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 一時的なエラーでリトライすべきAWS例外のリスト（正確なエラー名と部分一致の両方）
            retry_exceptions = [
                "ThrottlingException",
                "TooManyRequestsException",
                "ServiceUnavailableException", 
                "InternalServerException",
                "ConnectionError",
                "Timeout",
                "dependencyFailedException",
                "InternalFailureException",
                "ResourceInUseException",
                "ResourceLimitExceededException"
            ]
            
            # 特に重要なエラーメッセージのパターン - これらが含まれる場合は常にリトライ
            critical_patterns = [
                "failed to process EventStream",
                "stream processing error",
                "binary data",
                "connection reset",
                "network error",
                "timeout",
                "socket error",
                "rate exceeded",
                "request throttled"
            ]
            
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    # 特殊なエージェント設定を調整
                    if attempt > 0 and 'invoke_agent' in func.__name__:
                        # Agentの呼び出しの場合、リトライ時にセッションIDを変える
                        if 'sessionId' in kwargs:
                            # 既存のセッションIDの末尾に試行回数を追加
                            original_session = kwargs['sessionId']
                            kwargs['sessionId'] = f"{original_session}_retry{attempt}"
                            logger.info(f"セッションIDを変更: {kwargs['sessionId']}")
                            
                        # トレースを有効化（2回目のリトライから）
                        if attempt >= 1:
                            kwargs['enableTrace'] = True
                    
                    # 関数を実行
                    result = func(*args, **kwargs)
                    
                    # 結果がEventStreamの場合は、特別な処理を追加
                    if hasattr(result, 'get') and callable(result.get) and 'body' in result:
                        logger.info(f"レスポンスにbodyキーを検出: EventStreamの可能性があります")
                    
                    # 結果が辞書で、明確なエラー指標を含む場合は例外を発生させる
                    if isinstance(result, dict) and ('error' in result or 'Error' in result):
                        error_content = result.get('error') or result.get('Error')
                        logger.warning(f"API呼び出し結果にエラーを検出: {error_content}")
                        
                        # エラー内容に基づいてリトライ判定
                        error_str = str(error_content)
                        should_retry = any(pattern.lower() in error_str.lower() for pattern in retry_exceptions + critical_patterns)
                        
                        if should_retry and attempt < max_retries:
                            logger.warning(f"レスポンスエラーのためリトライします: {error_str[:100]}")
                            raise ValueError(f"Response error: {error_str}")
                            
                    # 結果を返す
                    return result
                    
                except Exception as e:
                    last_exception = e
                    error_name = type(e).__name__
                    error_msg = str(e)
                    
                    # リトライ判定: 例外名、メッセージ内容、重要パターンをチェック
                    retry_error = (
                        any(err_name in error_name for err_name in retry_exceptions) or
                        any(err_name in error_msg for err_name in retry_exceptions) or
                        any(pattern.lower() in error_msg.lower() for pattern in critical_patterns)
                    )
                                      
                    if retry_error and attempt < max_retries:
                        # 指数バックオフ + ジッター
                        backoff = base_delay * (2 ** attempt)
                        # ランダムなジッターを追加
                        jitter_value = random.uniform(0, jitter)
                        wait_time = backoff + jitter_value
                        
                        logger.warning(
                            f"AWS API呼び出しエラー: {error_name}. "
                            f"リトライ {attempt+1}/{max_retries}: "
                            f"{wait_time:.2f}秒後に再試行します。エラー: {error_msg[:100]}"
                        )
                        
                        time.sleep(wait_time)
                        continue
                    else:
                        # リトライ不可能なエラーか最大リトライ回数に達した場合
                        if attempt == max_retries:
                            logger.error(f"最大リトライ回数({max_retries}回)に達しました: {error_name}")
                        else:
                            logger.error(f"リトライ不可能なエラー: {error_name}")
                        
                        # エラーの詳細をログに残す
                        logger.error(f"エラー詳細: {error_msg}")
                        
                        # 元の例外を再度発生させる
                        raise last_exception
                        
            # このコードが実行されることはないが、念のため
            raise last_exception
            
        return wrapper
    
    return decorator