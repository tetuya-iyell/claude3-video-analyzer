/**
 * セッション管理用ヘルパー関数
 * ブラウザのローカルストレージにセッションIDを永続化して、DynamoDBとの同期を確実にする
 */

// ページロード時に既存のセッションIDをチェックし、必要に応じて初期化
function initializeSession() {
    // Cookieからセッションを取得する関数
    function getSessionIdFromCookie() {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'session') {
                try {
                    // Cookieの値はURLエンコードされているのでデコードする
                    return decodeURIComponent(value);
                } catch (e) {
                    console.error('セッションCookieのデコードに失敗:', e);
                }
            }
        }
        return null;
    }

    // セッションIDを取得（複数のストレージから探す）
    let sessionId = localStorage.getItem('yukkuri_session_id') || 
                    sessionStorage.getItem('yukkuri_session_id') || 
                    getSessionIdFromCookie();

    // セッションIDがなければ新しく生成
    if (!sessionId) {
        sessionId = generateUUID();
        console.log('新しいセッションIDを生成しました:', sessionId);
    } else {
        console.log('既存のセッションIDを読み込みました:', sessionId);
    }

    // すべてのストレージに保存して一貫性を確保
    localStorage.setItem('yukkuri_session_id', sessionId);
    sessionStorage.setItem('yukkuri_session_id', sessionId);
    
    return sessionId;
}

// セッションIDを取得
function getSessionId() {
    return localStorage.getItem('yukkuri_session_id') || 
           sessionStorage.getItem('yukkuri_session_id') || 
           initializeSession(); // なければ初期化
}

// UUIDを生成する関数
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// セッションIDをすべての非同期リクエストに追加するヘルパー関数
function appendSessionToRequest(requestBody) {
    if (!requestBody) {
        requestBody = {};
    }
    
    requestBody.session_id = getSessionId();
    return requestBody;
}

// グローバルスコープにセッションヘルパーを公開
window.SessionManager = {
    initializeSession,
    getSessionId,
    appendSessionToRequest
};

// ページ読み込み時に自動的にセッションを初期化
document.addEventListener('DOMContentLoaded', function() {
    initializeSession();
    console.log('セッション管理システムを初期化しました。セッションID:', getSessionId());
});