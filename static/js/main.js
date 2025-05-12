document.addEventListener('DOMContentLoaded', () => {
    // セッション管理を初期化
    window.SessionManager.initializeSession();
    console.log('セッションID:', window.SessionManager.getSessionId());
    // グローバルイベントリスナーを追加して章の切り替えに関連するクリーンアップを強化
    document.addEventListener('click', function(e) {
        // クリック要素がチャプターアイテム（または子要素）の場合
        if (e.target.closest('.chapter-item')) {
            console.log('チャプターアイテムクリック検出: フィードバック表示をリセットします');

            // フィードバック関連要素を即座にリセット
            const feedbackList = document.getElementById('feedback-list');
            const historyHeader = document.getElementById('history-header');

            if (feedbackList) feedbackList.innerHTML = '';
            if (historyHeader) historyHeader.style.display = 'none';
        }
    });
    // トースト通知機能の追加
    function showToast(message, type = 'info') {
        // トーストコンテナの作成（なければ）
        let toastContainer = document.getElementById('toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            document.body.appendChild(toastContainer);
        }

        // トースト要素の作成
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;

        // トーストをコンテナに追加
        toastContainer.appendChild(toast);

        // 表示アニメーション
        setTimeout(() => {
            toast.classList.add('show');
        }, 10);

        // 自動消去
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => {
                toast.remove();
            }, 300);
        }, 3000);
    }

    // グローバルアクセスのためにウィンドウオブジェクトに関数を追加
    window.showToast = showToast;

    // DOM要素の参照
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const filename = document.getElementById('filename');
    const changeFileButton = document.getElementById('change-file');
    const normalPromptTextarea = document.getElementById('normal-prompt');
    const chaptersPromptTextarea = document.getElementById('chapters-prompt');
    const analyzeButton = document.getElementById('analyze-button');
    const loading = document.getElementById('loading');
    const resultsSection = document.getElementById('results-section');
    const resultsContent = document.getElementById('results-content');
    const copyButton = document.getElementById('copy-button');
    const newAnalysisButton = document.getElementById('new-analysis');
    const generateScriptsButton = document.getElementById('generate-scripts-button');
    const tabs = document.querySelectorAll('.tab');
    const tabContents = document.querySelectorAll('.tab-content');
    
    // 台本エディタ関連
    const scriptEditorSection = document.getElementById('script-editor-section');
    const chapterList = document.getElementById('chapter-list');
    const chapterTitle = document.getElementById('chapter-title');
    const chapterStatus = document.getElementById('chapter-status');
    const chapterSummary = document.getElementById('chapter-summary');
    const scriptTextarea = document.getElementById('script-textarea');
    const feedbackContainer = document.getElementById('feedback-container');
    const feedbackHeader = document.getElementById('feedback-header');
    const historyHeader = document.getElementById('history-header');
    const feedbackList = document.getElementById('feedback-list');
    const feedbackTextarea = document.getElementById('feedback-textarea');
    const analysisResult = document.getElementById('analysis-result');
    const analyzeScriptButton = document.getElementById('analyze-script-button');
    const approveScriptButton = document.getElementById('approve-script-button');
    const rejectScriptButton = document.getElementById('reject-script-button');
    const applyImprovementButton = document.getElementById('apply-improvement-button');

    // 章ごとのフィードバックデータ管理用オブジェクト

    // 選択された動画ファイル
    let selectedFile = null;
    // 現在の解析タイプ（normalまたはchapters）
    let currentAnalyzeType = 'normal';
    // 解析結果テキスト
    let analysisText = '';
    // 章情報
    let chapters = [];
    // 台本データ
    let scripts = [];
    // 現在選択されている章インデックス
    let currentChapterIndex = -1;

    // タブ切り替え処理
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            // 現在のアクティブタブを非アクティブに
            document.querySelector('.tab.active').classList.remove('active');
            document.querySelector('.tab-content.active').classList.remove('active');
            
            // クリックされたタブをアクティブに
            tab.classList.add('active');
            const tabId = tab.getAttribute('data-tab');
            document.getElementById(tabId).classList.add('active');
            
            // 解析タイプを設定
            currentAnalyzeType = tabId === 'chapters-tab' ? 'chapters' : 'normal';
        });
    });

    // ドラッグ&ドロップイベント
    ['dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    dropZone.addEventListener('dragover', () => {
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', handleDrop);
    fileInput.addEventListener('change', handleFileSelect);

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const file = dt.files[0];
        handleFile(file);
        dropZone.classList.remove('drag-over');
    }

    function handleFileSelect(e) {
        const file = e.target.files[0];
        handleFile(file);
    }

    function handleFile(file) {
        if (file && file.type === 'video/mp4') {
            selectedFile = file;
            filename.textContent = file.name;
            fileInfo.classList.remove('hidden');
            dropZone.classList.add('hidden');
            analyzeButton.disabled = false;
        } else {
            alert('MP4形式のファイルのみ対応しています。');
        }
    }

    // ファイル変更ボタン
    changeFileButton.addEventListener('click', () => {
        selectedFile = null;
        fileInput.value = '';
        fileInfo.classList.add('hidden');
        dropZone.classList.remove('hidden');
        analyzeButton.disabled = true;
    });

    // 解析開始ボタン
    analyzeButton.addEventListener('click', startAnalysis);

    function startAnalysis() {
        // 台本生成ボタンを初期状態では非表示に
        generateScriptsButton.classList.add('hidden');
        if (!selectedFile) return;

        // 解析開始前の表示更新
        analyzeButton.disabled = true;
        loading.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        resultsContent.innerHTML = '';

        // FormDataの作成
        const formData = new FormData();
        formData.append('video', selectedFile);
        formData.append('analyze_type', currentAnalyzeType);

        // 解析タイプに基づいてプロンプトを選択
        if (currentAnalyzeType === 'chapters') {
            formData.append('prompt', chaptersPromptTextarea.value);
            formData.append('chapters_prompt', chaptersPromptTextarea.value);
        } else {
            formData.append('prompt', normalPromptTextarea.value);
        }

        // 解析セクションを表示
        resultsSection.classList.remove('hidden');
        resultsContent.innerHTML = '';

        // 解析タイプに基づいて異なるエンドポイントを使用
        const apiEndpoint = currentAnalyzeType === 'chapters' ? '/api/analyze/chapters' : '/api/analyze';

        // Server-Sent Events (SSE) を使用して進行状況をリアルタイムで表示
        fetch(apiEndpoint, {
            method: 'POST',
            body: formData
        }).then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error ${response.status}`);
            }

            // テキストストリームとして処理
            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = '';

            function processStream() {
                return reader.read().then(({ done, value }) => {
                    if (done) {
                        console.log("Stream complete");
                        loading.classList.add('hidden');
                        analyzeButton.disabled = false;

                        // 章立て解析完了時に台本生成ボタンを表示
                        if (currentAnalyzeType === 'chapters') {
                            generateScriptsButton.classList.remove('hidden');
                            analysisText = resultsContent.innerText;
                            console.log('章立て解析完了: 台本生成ボタンを表示');
                        }

                        return;
                    }

                    // デコードして現在のバッファに追加
                    buffer += decoder.decode(value, { stream: true });

                    // イベントの処理 - 複数のイベントが含まれている可能性があるため、繰り返し処理
                    // 各SSEイベントは "data: {...}\n\n" 形式
                    let eventEndIndex;
                    while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
                        const eventData = buffer.substring(0, eventEndIndex);
                        buffer = buffer.substring(eventEndIndex + 2); // '\n\n'の後ろを新しいバッファに

                        // 'data: ' プレフィックスを取り除く
                        if (eventData.startsWith('data: ')) {
                            const jsonData = eventData.substring(6); // 'data: 'の長さ

                            try {
                                const data = JSON.parse(jsonData);
                                console.log("受信したSSEデータ:", data);

                                if (data.text) {
                                    // テキストをリアルタイムで表示
                                    resultsContent.textContent += data.text;
                                    // スクロール位置を最下部に調整
                                    resultsContent.scrollTop = resultsContent.scrollHeight;
                                }

                                if (data.error) {
                                    // エラー表示
                                    resultsContent.innerHTML += `<div class="error">Error: ${data.error}</div>`;
                                    loading.classList.add('hidden');
                                    analyzeButton.disabled = false;
                                }

                                if (data.complete) {
                                    // 完了処理
                                    loading.classList.add('hidden');
                                    analyzeButton.disabled = false;

                                    // 章立て解析完了時に台本生成ボタンを表示
                                    if (currentAnalyzeType === 'chapters') {
                                        generateScriptsButton.classList.remove('hidden');
                                        analysisText = resultsContent.innerText;
                                        console.log('章立て解析完了: 台本生成ボタンを表示');
                                    }
                                }
                            } catch (error) {
                                console.error("JSON parse error:", error, "Raw data:", jsonData);
                            }
                        }
                    }

                    // 継続処理
                    return processStream();
                }).catch(error => {
                    console.error("Stream error:", error);
                    resultsContent.innerHTML += `<div class="error">Stream error: ${error.message}</div>`;
                    loading.classList.add('hidden');
                    analyzeButton.disabled = false;
                });
            }

            return processStream();
        }).catch(error => {
            console.error('Error:', error);
            resultsContent.innerHTML += `<div class="error">Error: ${error.message}</div>`;
            loading.classList.add('hidden');
            analyzeButton.disabled = false;
        });
    }

    // 結果コピーボタン
    copyButton.addEventListener('click', () => {
        const text = resultsContent.innerText;
        navigator.clipboard.writeText(text)
            .then(() => {
                const originalText = copyButton.innerText;
                copyButton.innerText = 'コピーしました！';
                setTimeout(() => {
                    copyButton.innerText = originalText;
                }, 2000);
            })
            .catch(err => {
                console.error('コピーに失敗しました:', err);
                alert('結果のコピーに失敗しました。');
            });
    });

    // 新しい解析ボタン
    newAnalysisButton.addEventListener('click', () => {
        selectedFile = null;
        fileInput.value = '';
        fileInfo.classList.add('hidden');
        dropZone.classList.remove('hidden');
        analyzeButton.disabled = true;
        resultsSection.classList.add('hidden');
        resultsContent.innerHTML = '';
        scriptEditorSection.style.display = 'none';
        // 台本生成ボタンも非表示に
        generateScriptsButton.classList.add('hidden');
    });
    
    // この関数は直接使わなくなりました - コメントアウトして参考として残しておく
    /*
    function showScriptGenerationOption() {
        if (currentAnalyzeType === 'chapters') {
            // 章立て解析の場合のみ、台本生成ボタンを表示
            generateScriptsButton.classList.remove('hidden');
            // 解析テキストを保存
            analysisText = resultsContent.innerText;
        } else {
            generateScriptsButton.classList.add('hidden');
        }
    }
    */
    
    // 台本生成ボタン
    generateScriptsButton.addEventListener('click', () => {
        // デバッグ用: リクエスト内容をコンソールに出力
        console.log('解析テキスト:', analysisText);
        console.log('解析テキスト長さ:', analysisText ? analysisText.length : 0);

        // 解析テキストから章構造を抽出するAPIを呼び出す
        fetch('/api/bedrock-scripts/analyze-chapters', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                analysis_text: analysisText
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // 章情報を保存
                chapters = data.chapters;
                console.log("章構造の抽出成功:", chapters);
                
                // 台本エディタUIを表示
                setupScriptEditor();
            } else {
                alert('章構造の抽出に失敗しました: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('章構造の抽出中にエラーが発生しました。');
        });
    });
    
    // 台本エディタUIのセットアップ
    function setupScriptEditor() {
        // 台本編集セクションを表示
        scriptEditorSection.style.display = 'block';

        // チャプターリストを生成
        renderChapterList();

        // フィードバック関連のリセット - 初期状態で確実にクリアしておく
        const feedbackHeader = document.getElementById('feedback-header');
        const historyHeader = document.getElementById('history-header');
        const feedbackList = document.getElementById('feedback-list');
        const feedbackContainer = document.getElementById('feedback-container');

        if (feedbackHeader) feedbackHeader.textContent = 'フィードバック';
        if (historyHeader) historyHeader.style.display = 'none';
        if (feedbackList) feedbackList.innerHTML = '';
        if (feedbackContainer) feedbackContainer.setAttribute('data-chapter', '-1');

        // 最初のチャプターを選択
        if (chapters.length > 0) {
            selectChapter(0);
        }

        // 解析結果セクションを隠す（任意）
        // resultsSection.classList.add('hidden');
    }

    // DynamoDBからスクリプトの最新状態を取得して同期する
    function syncScriptWithDynamoDB(index, callback) {
        console.log(`DynamoDBとの同期を開始: 章${index + 1}`);

        // SessionManagerからセッションIDを取得
        const sessionId = window.SessionManager.getSessionId();

        // セッションIDの取得状況をログに残す（デバッグ用）
        console.log(`SessionManagerから取得したセッションID: ${sessionId}`);

        if (!sessionId) {
            console.log('セッションIDが存在しないため同期をスキップします');
            if (callback) callback(null);
            return;
        }

        // 非同期でAPIを呼び出す（メイン処理を妨げないようにする）
        fetch('/api/bedrock-scripts/sync-with-dynamodb', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: index,
                session_id: sessionId  // セッションIDを明示的にリクエストボディに含める
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log(`DynamoDB同期成功: 章${index + 1}`);

                // スクリプトデータが返却された場合は更新
                if (data.script) {
                    console.log(`章${index + 1}のスクリプトデータを更新します:`, data.script);

                    // スクリプトデータを更新
                    scripts[index] = data.script;

                    // フィードバックの数を確認してログに出力（デバッグ用）
                    const feedbackCount = data.script.feedback ? data.script.feedback.length : 0;
                    console.log(`章${index + 1}のフィードバック数: ${feedbackCount}`);

                    // フィードバック配列が存在するか確認して初期化（必要な場合）
                    if (!data.script.feedback) {
                        data.script.feedback = [];
                        console.log(`章${index + 1}のフィードバック配列を初期化しました`);
                    }

                    // コールバック関数が指定されていれば実行
                    if (callback) callback(data.script);
                } else {
                    if (callback) callback(null);
                }
            } else {
                console.warn(`DynamoDB同期エラー: ${data.error || '不明なエラー'}`);
                if (callback) callback(null);
            }
        })
        .catch(error => {
            console.error(`DynamoDB同期中の通信エラー:`, error);
            if (callback) callback(null);
        });
    }
    
    // チャプターリストを描画
    function renderChapterList() {
        chapterList.innerHTML = '';
        
        chapters.forEach((chapter, index) => {
            const chapterItem = document.createElement('div');
            chapterItem.className = 'chapter-item';
            chapterItem.textContent = `${chapter.chapter_num}. ${chapter.chapter_title}`;
            chapterItem.dataset.index = index;
            
            // 台本の状態に応じてクラスを追加
            const script = scripts[index];
            if (script) {
                if (script.status === 'approved') {
                    chapterItem.classList.add('approved');
                } else if (script.status === 'rejected') {
                    chapterItem.classList.add('rejected');
                }
            }
            
            // クリックイベント
            chapterItem.addEventListener('click', () => {
                selectChapter(index);
            });
            
            chapterList.appendChild(chapterItem);
        });
    }
    
    // チャプター選択処理
    function selectChapter(index) {
        console.log(`チャプター切替: ${currentChapterIndex} -> ${index}`);

        // 現在選択中のチャプターのハイライトを解除
        const currentSelected = chapterList.querySelector('.active');
        if (currentSelected) {
            currentSelected.classList.remove('active');
        }

        // 新しいチャプターをハイライト
        const newSelected = chapterList.querySelector(`[data-index="${index}"]`);
        if (newSelected) {
            newSelected.classList.add('active');
        }

        // 現在のチャプターインデックスを更新
        currentChapterIndex = index;
        const chapter = chapters[index];

        // チャプタータイトルとサマリーを表示
        chapterTitle.textContent = `${chapter.chapter_num}. ${chapter.chapter_title}`;
        chapterSummary.textContent = chapter.chapter_summary;

        // 【重要】フィードバック関連を即座にクリア（チャプター切替での表示問題対策）
        const feedbackHeader = document.getElementById('feedback-header');
        const feedbackList = document.getElementById('feedback-list');
        const historyHeader = document.getElementById('history-header');

        // 表示をリセット - この時点でDOM要素を完全にクリア
        console.log(`章切替時のフィードバッククリア: 次の章=${index + 1}`);

        // 確実にフィードバックヘッダーを更新
        feedbackHeader.textContent = `フィードバック - 章${index + 1}`;

        // フィードバックリストを強制的に空に
        feedbackList.innerHTML = '';

        // 修正履歴ヘッダーも必ず非表示に
        historyHeader.style.display = 'none';
        historyHeader.setAttribute('data-chapter', index);

        // フィードバックコンテナ自体にも章番号を紐付け
        const feedbackContainer = document.getElementById('feedback-container');
        if (feedbackContainer) {
            feedbackContainer.setAttribute('data-chapter', index);
        }

        // フィードバック入力欄をクリア
        feedbackTextarea.value = '';

        // 【重要】常にDynamoDBとの同期を試みる - 特に完了した章の場合
        console.log(`章${index + 1}のデータをDynamoDBと同期します（修正履歴取得のため）`);

        // DynamoDBからフィードバック履歴を取得して最新状態にする
        // 同期が完了した後の処理をコールバックで渡す
        syncScriptWithDynamoDB(index, function(updatedScript) {
            // 同期が成功して更新されたスクリプトがある場合
            if (updatedScript) {
                // スクリプトを更新して表示
                scripts[index] = updatedScript;

                // フィードバックが存在しない場合、空配列を初期化
                if (!updatedScript.feedback) {
                    updatedScript.feedback = [];
                    console.log(`章${index + 1}のフィードバック配列を初期化しました（DynamoDBから更新後）`);
                } else {
                    console.log(`章${index + 1}のフィードバック数: ${updatedScript.feedback.length}（DynamoDBから更新後）`);
                }

                // スクリプトを表示（フィードバック履歴も表示される）
                displayScript(updatedScript);
            } else {
                // 同期できなかった場合は既存のスクリプトを表示するか生成
                const script = scripts[index];
                if (script) {
                    // フィードバックが存在しない場合、空配列を初期化
                    if (!script.feedback) {
                        script.feedback = [];
                        console.log(`章${index + 1}のフィードバック配列を初期化しました（ローカルスクリプト）`);
                    }

                    displayScript(script);
                } else {
                    generateScript(index);
                }
            }
        });
    }
    
    // スクリプトデータを表示
    function displayScript(script) {
        // 動画時間の設定を更新（保存されている値があれば）
        const durationInput = document.getElementById('duration-input');
        if (script.duration_minutes) {
            durationInput.value = script.duration_minutes;
            console.log(`動画時間を更新: ${script.duration_minutes}分`);
        }

        // 改善された台本がある場合は、台本欄に表示し、適用ボタンも表示
        if (script.improved_script) {
            // 元の台本を保存
            script._original_content = script.script_content;
            // 改善された台本を表示
            scriptTextarea.value = script.improved_script;
            // 改善適用ボタンを表示
            applyImprovementButton.classList.remove('hidden');
            applyImprovementButton.classList.add('highlight');
            // ステータス表示を更新（改善中に変更）
            updateScriptStatus('improved');
        } else {
            // 通常の台本を表示
            scriptTextarea.value = script.script_content;
            // ステータス表示
            updateScriptStatus(script.status);
            // 改善適用ボタンを非表示に
            applyImprovementButton.classList.add('hidden');
            applyImprovementButton.classList.remove('highlight');
        }

        // フィードバック関連の表示処理
        console.log(`displayScript呼び出し: 章${currentChapterIndex}, フィードバック数=${script.feedback ? script.feedback.length : 0}`);

        // 【重要】DOM要素を毎回直接取得して確実に参照（キャッシュを使わない）
        const feedbackHeader = document.getElementById('feedback-header');
        const historyHeader = document.getElementById('history-header');
        const feedbackList = document.getElementById('feedback-list');
        const feedbackContainer = document.getElementById('feedback-container');

        // 必ず最初に全てクリア（どんな状態でも最初に必ず空にする）
        console.log(`displayScript内でのフィードバッククリア実行: 章${currentChapterIndex + 1}`);
        feedbackHeader.textContent = '';
        historyHeader.style.display = 'none';
        feedbackList.innerHTML = '';

        // フィードバックコンテナ全体に章番号を紐付け（デバッグ用、表示問題の追跡に使用）
        if (feedbackContainer) {
            feedbackContainer.setAttribute('data-chapter', currentChapterIndex);
        }

        // フィードバックヘッダー更新（確実に現在の章番号を反映）
        feedbackHeader.textContent = `フィードバック - 章${currentChapterIndex + 1}`;

        // デバッグ: 章の表示状態を詳細に確認
        console.log(`章${currentChapterIndex + 1}の表示: スクリプト状態=${script.status}, フィードバック数=${script.feedback ? script.feedback.length : 0}`);

        // 過去のフィードバック履歴を表示すべきか判断 - すべての状態で表示するよう条件を緩和
        const hasFeedback = script.feedback && script.feedback.length > 0;

        // 修正履歴ヘッダーの表示/非表示（フィードバックがある場合は常に表示）
        if (hasFeedback) {
            // 章番号を明示的にヘッダーに埋め込み
            historyHeader.textContent = `章${currentChapterIndex + 1}の修正履歴`;

            // 強制的に表示するために徹底的な方法で表示設定
            setTimeout(() => {
                // 非同期で処理して確実に適用されるようにする
                historyHeader.style.display = 'block';
                historyHeader.style.visibility = 'visible';
                historyHeader.style.removeProperty('display');
                historyHeader.removeAttribute('style');
                historyHeader.setAttribute('style', 'display: block !important; visibility: visible !important;');

                // DOMに直接表示指定を適用
                historyHeader.classList.remove('hidden');

                console.log(`章${currentChapterIndex + 1}の修正履歴ヘッダーを強制表示しました`);
            }, 100);

            // さらに章番号を属性として保存（デバッグ用）
            historyHeader.setAttribute('data-chapter', currentChapterIndex);
            console.log(`章${currentChapterIndex + 1}の修正履歴ヘッダーを表示`);
        } else {
            historyHeader.style.display = 'none';
            console.log(`章${currentChapterIndex + 1}にはフィードバックがないため修正履歴ヘッダーを非表示`);
        }

        // 現在の章のフィードバック履歴を表示する
        if (hasFeedback) {
            // 詳細なデバッグ情報を表示
            console.log(`章${currentChapterIndex + 1}のフィードバック表示: ${script.feedback.length}件`);
            console.log(`フィードバック配列の内容:`, JSON.stringify(script.feedback));

            // 新しい順に表示
            const reversedFeedback = [...script.feedback].reverse();

            // 章情報をデータ属性として各フィードバック要素に埋め込む
            reversedFeedback.forEach((feedback, index) => {
                const feedbackItem = document.createElement('div');
                feedbackItem.className = 'script-feedback';
                feedbackItem.setAttribute('data-chapter', currentChapterIndex);

                // 履歴番号（章番号を含める）
                const feedbackNumber = document.createElement('div');
                feedbackNumber.className = 'feedback-number';
                feedbackNumber.textContent = `#${reversedFeedback.length - index} (章${currentChapterIndex + 1})`;
                feedbackItem.appendChild(feedbackNumber);

                // フィードバック内容
                const feedbackContent = document.createElement('div');
                feedbackContent.className = 'feedback-content';
                feedbackContent.textContent = feedback;
                feedbackItem.appendChild(feedbackContent);

                feedbackList.appendChild(feedbackItem);
            });
        } else {
            // 履歴がない場合のメッセージ（章番号を含める）
            const noFeedback = document.createElement('p');
            noFeedback.textContent = `章${currentChapterIndex + 1}の修正履歴はありません`;
            noFeedback.className = 'no-feedback-message';
            noFeedback.setAttribute('data-chapter', currentChapterIndex);
            feedbackList.appendChild(noFeedback);
            console.log(`章${currentChapterIndex + 1}に修正履歴がない旨を表示`);
        }

        // フィードバック入力欄はクリアして表示
        feedbackTextarea.value = '';
        feedbackTextarea.style.display = 'block';

        // 分析結果の表示
        if (script.analysis) {
            analysisResult.textContent = script.analysis;
            analysisResult.classList.remove('hidden');
        } else {
            analysisResult.classList.add('hidden');
        }
    }
    
    // スクリプトステータスを更新
    function updateScriptStatus(status) {
        chapterStatus.textContent = '';
        chapterStatus.className = '';

        if (status === 'approved') {
            chapterStatus.textContent = '承認済み';
            chapterStatus.className = 'success-message';
        } else if (status === 'rejected') {
            chapterStatus.textContent = '修正依頼中';
            chapterStatus.className = 'error-message';
        } else if (status === 'review') {
            chapterStatus.textContent = 'レビュー中';
        } else if (status === 'improved') {
            chapterStatus.textContent = '改善された台本';
            chapterStatus.className = 'success-message';
        } else if (status === 'completed') {
            chapterStatus.textContent = '編集完了';
            chapterStatus.className = 'complete-message';
        } else if (status === 'draft') {
            chapterStatus.textContent = '下書き';
        } else if (status === 'generating') {
            chapterStatus.textContent = '生成中...';
        }
    }
    
    // 台本を生成する
    function generateScript(index) {
        console.log('台本生成開始:', index);

        // ステータス表示を「生成中」に更新
        updateScriptStatus('generating');

        // テキストエリアをローディング状態に
        scriptTextarea.value = '台本を生成中...\n\nAIが台本を執筆しています。しばらくお待ちください...';
        scriptTextarea.disabled = true;

        // ローディングアニメーションの作成
        const loadingAnimation = document.createElement('div');
        loadingAnimation.className = 'script-loading-animation';
        loadingAnimation.innerHTML = `
            <div class="loading-dots">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
            <p>台本を生成中...</p>
        `;

        // スクリプトエリアに直接ローディングアニメーションを追加
        const scriptContentContainer = document.querySelector('.script-content');
        // 既存のローディングアニメーションを削除（念のため）
        const existingAnimation = scriptContentContainer.querySelector('.script-loading-animation');
        if (existingAnimation) {
            existingAnimation.remove();
        }

        // テキストエリアの前にアニメーションを挿入
        scriptContentContainer.insertBefore(loadingAnimation, scriptTextarea);

        // テキストエリアを一時的に非表示
        scriptTextarea.style.display = 'none';

        // 動画時間を取得（分単位）
        const durationInput = document.getElementById('duration-input');
        const durationMinutes = parseInt(durationInput.value) || 3;

        // 現在のチャプターをハイライト表示
        const chapterItems = document.querySelectorAll('.chapter-item');
        chapterItems.forEach((item) => {
            if (parseInt(item.dataset.index) === index) {
                item.classList.add('generating');
            }
        });

        // デバッグ: リクエストの詳細をコンソールに表示
        const requestBody = {
            chapter_index: index,
            chapters: chapters,
            duration_minutes: durationMinutes
        };
        console.log('台本生成リクエスト:', requestBody);

        fetch('/api/bedrock-scripts/generate-script', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        })
        .then(response => {
            console.log('台本生成レスポンスステータス:', response.status);
            return response.json();
        })
        .then(data => {
            // デバッグ: レスポンスの詳細をコンソールに表示
            console.log('台本生成レスポンス:', data);

            // ローディングアニメーション削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }

            // テキストエリアを再表示
            scriptTextarea.style.display = '';

            // ハイライト表示を解除
            const chapterItems = document.querySelectorAll('.chapter-item');
            chapterItems.forEach((item) => {
                if (parseInt(item.dataset.index) === index) {
                    item.classList.remove('generating');
                }
            });

            // エラーの場合は詳細なトレースバックを表示（デバッグ用）
            if (data.error && data.traceback) {
                console.error('Python Error:', data.error);
                console.error('Traceback:', data.traceback);
                scriptTextarea.value = `エラー: ${data.error}\n\nトレースバック:\n${data.traceback}`;
                scriptTextarea.disabled = false;
                updateScriptStatus('error');
                return;
            }

            if (data.success) {
                // 生成された台本を保存
                scripts[index] = data.script;

                // 表示を更新
                scriptTextarea.value = data.script.script_content;
                scriptTextarea.disabled = false;

                // 完了メッセージを表示
                console.log('台本生成が完了しました');

                // トースト通知を表示
                showToast('台本が正常に生成されました！', 'success');

                // ステータス表示
                updateScriptStatus(data.script.status);

                // チャプターリストの表示を更新
                renderChapterList();
            } else {
                scriptTextarea.value = '台本の生成に失敗しました: ' + data.error;
                scriptTextarea.disabled = false;
                updateScriptStatus('error');

                // エラー通知を表示
                showToast('台本の生成に失敗しました: ' + data.error, 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);

            // ローディングアニメーション削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }

            // テキストエリアを再表示
            scriptTextarea.style.display = '';
            scriptTextarea.value = '台本の生成中にエラーが発生しました。';
            scriptTextarea.disabled = false;
            updateScriptStatus('error');

            // エラー通知を表示
            showToast('台本の生成中にエラーが発生しました。', 'error');

            // ハイライト表示を解除
            const chapterItems = document.querySelectorAll('.chapter-item');
            chapterItems.forEach((item) => {
                if (parseInt(item.dataset.index) === index) {
                    item.classList.remove('generating');
                }
            });
        });
    }
    
    // 台本分析ボタン
    analyzeScriptButton.addEventListener('click', () => {
        if (currentChapterIndex < 0) return;
        
        // 現在の台本内容を取得
        const scriptContent = scriptTextarea.value;
        
        // 分析中表示
        analysisResult.textContent = '台本を分析中...';
        analysisResult.classList.remove('hidden');
        
        // 動画時間を取得（分単位）
        const durationInput = document.getElementById('duration-input');
        const durationMinutes = parseInt(durationInput.value) || 3;
        
        fetch('/api/bedrock-scripts/analyze-script', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                script_content: scriptContent,
                duration_minutes: durationMinutes // 動画時間パラメータを追加
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // 分析結果を表示
                analysisResult.textContent = data.analysis;
                
                // スクリプトのpassed状態を更新
                scripts[currentChapterIndex].passed = data.passed;
                scripts[currentChapterIndex].analysis = data.analysis;
                
                // 結果に応じたスタイル適用
                if (data.passed) {
                    analysisResult.className = 'script-feedback success-message';
                } else {
                    analysisResult.className = 'script-feedback error-message';
                }
            } else {
                analysisResult.textContent = '分析に失敗しました: ' + data.error;
                analysisResult.className = 'script-feedback error-message';
            }
        })
        .catch(error => {
            console.error('Error:', error);
            analysisResult.textContent = '分析中にエラーが発生しました。';
            analysisResult.className = 'script-feedback error-message';
        });
    });
    
    // 台本承認ボタン
    approveScriptButton.addEventListener('click', () => {
        if (currentChapterIndex < 0) return;

        // 現在の台本内容を取得（エディタからの最新内容）
        const currentContent = scriptTextarea.value;

        // 現在表示されている内容をscript_contentに保存（最新の変更を反映）
        scripts[currentChapterIndex].script_content = currentContent;

        // フィードバックを取得（空でも可）
        const feedbackText = feedbackTextarea.value || '承認しました。';

        // アニメーション用のクラスを作成
        const loadingAnimation = document.createElement('div');
        loadingAnimation.className = 'script-loading-animation';
        loadingAnimation.innerHTML = `
            <div class="loading-dots">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
            <p>台本を承認中...</p>
        `;

        // テキストエリアのコンテナを取得
        const scriptContainer = scriptTextarea.parentElement;

        // テキストエリアを非表示にして、アニメーションを表示
        scriptTextarea.style.display = 'none';
        scriptContainer.appendChild(loadingAnimation);

        // 動画時間を取得（分単位）
        const durationInput = document.getElementById('duration-input');
        const durationMinutes = parseInt(durationInput.value) || 3;

        fetch('/api/bedrock-scripts/submit-feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                feedback: feedbackText,
                is_approved: true,
                duration_minutes: durationMinutes // 動画時間パラメータを追加
            })
        })
        .then(response => response.json())
        .then(data => {
            // アニメーションを削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }

            // テキストエリアを再び表示
            scriptTextarea.style.display = '';

            if (data.success) {
                // スクリプトの状態を更新
                scripts[currentChapterIndex].status = 'approved';

                // UI更新
                updateScriptStatus('approved');
                renderChapterList();

                // 改善適用ボタンを非表示に
                applyImprovementButton.classList.add('hidden');
                applyImprovementButton.classList.remove('highlight');

                // 承認メッセージを表示
                showToast('台本が承認されました！ DynamoDBに保存されました。', 'success');

                // フィードバックリストを更新（最新の内容を表示）
                if (!scripts[currentChapterIndex].feedback) {
                    scripts[currentChapterIndex].feedback = [];
                }
                if (feedbackText && feedbackText !== '承認しました。') {
                    scripts[currentChapterIndex].feedback.push(feedbackText);
                }

                // フィードバック入力欄をクリア
                feedbackTextarea.value = '';

                // 注意: 次のチャプターへの自動選択を無効化
                // 承認後に次の章が自動選択されると、再度台本生成APIが呼ばれてしまうため
                /*
                if (currentChapterIndex + 1 < chapters.length) {
                    selectChapter(currentChapterIndex + 1);
                }
                */
            } else {
                showToast('承認に失敗しました: ' + data.error, 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);

            // アニメーションを削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }

            // テキストエリアを再び表示
            scriptTextarea.style.display = '';

            showToast('承認処理中にエラーが発生しました。', 'error');
        });
    });
    
    // 修正依頼ボタン
    rejectScriptButton.addEventListener('click', () => {
        if (currentChapterIndex < 0) return;

        // フォーカスを当てる
        feedbackTextarea.focus();

        // 既にフィードバックが入力されているか確認
        const feedbackText = feedbackTextarea.value;
        if (!feedbackText) {
            // フィードバックが入力されていない場合は入力を促すだけ
            alert('フィードバックを入力してから再度「修正依頼」ボタンをクリックしてください。');
            return;
        }

        // アニメーション用のクラスを作成
        const loadingAnimation = document.createElement('div');
        loadingAnimation.className = 'script-loading-animation';
        loadingAnimation.innerHTML = `
            <div class="loading-dots">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
            <p>台本を改善中...</p>
        `;

        // 現在の台本内容を取得（エディタからの最新内容）
        const currentContent = scriptTextarea.value;

        // 現在表示されている内容をscript_contentに保存（最新の変更を反映）
        scripts[currentChapterIndex].script_content = currentContent;

        // テキストエリアのコンテナを取得
        const scriptContainer = scriptTextarea.parentElement;

        // テキストエリアを非表示にして、アニメーションを表示
        scriptTextarea.style.display = 'none';
        scriptContainer.appendChild(loadingAnimation);

        // ステータスを更新
        updateScriptStatus('rejected');

        // 動画時間を取得（分単位）
        const durationInput = document.getElementById('duration-input');
        const durationMinutes = parseInt(durationInput.value) || 3;

        fetch('/api/bedrock-scripts/submit-feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                feedback: feedbackText,
                is_approved: false,
                duration_minutes: durationMinutes // 動画時間パラメータを追加
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // アニメーションを削除
                const loadingAnimation = document.querySelector('.script-loading-animation');
                if (loadingAnimation) {
                    loadingAnimation.remove();
                }

                // テキストエリアを再び表示
                scriptTextarea.style.display = '';

                // スクリプトの状態を更新
                scripts[currentChapterIndex].status = 'improved';

                // フィードバックリストに追加
                if (!scripts[currentChapterIndex].feedback) {
                    scripts[currentChapterIndex].feedback = [];
                }
                scripts[currentChapterIndex].feedback.push(feedbackText);

                // 改善された台本があれば保存して表示
                if (data.improved_script) {
                    // 元の台本を保存
                    scripts[currentChapterIndex]._original_content = scripts[currentChapterIndex].script_content;
                    // 改善された台本を保存
                    scripts[currentChapterIndex].improved_script = data.improved_script;

                    // 成功メッセージの表示
                    showToast('フィードバックを受け付けました。改善された台本を表示します。', 'success');

                    // 改善適用ボタンを表示して目立たせる
                    applyImprovementButton.classList.remove('hidden');
                    applyImprovementButton.classList.add('highlight');

                    // 台本を更新（フィードバックリストも更新される）
                    displayScript(scripts[currentChapterIndex]);
                } else {
                    showToast('フィードバックを受け付けましたが、台本の改善に失敗しました。', 'error');
                    // 失敗の場合は元の状態に戻す
                    updateScriptStatus('rejected');
                    scriptTextarea.value = scripts[currentChapterIndex].script_content;
                }

                // UI更新
                renderChapterList();
            } else {
                // アニメーションを削除
                const loadingAnimation = document.querySelector('.script-loading-animation');
                if (loadingAnimation) {
                    loadingAnimation.remove();
                }

                // テキストエリアを再び表示
                scriptTextarea.style.display = '';
                scriptTextarea.value = scripts[currentChapterIndex].script_content;
                showToast('修正依頼に失敗しました: ' + data.error, 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);

            // アニメーションを削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }

            // テキストエリアを再び表示
            scriptTextarea.style.display = '';
            scriptTextarea.value = scripts[currentChapterIndex].script_content;
            showToast('修正依頼処理中にエラーが発生しました。', 'error');
        });
    });
    
    // 改善適用ボタン
    applyImprovementButton.addEventListener('click', () => {
        if (currentChapterIndex < 0) return;
        
        // アニメーション用のクラスを作成
        const loadingAnimation = document.createElement('div');
        loadingAnimation.className = 'script-loading-animation';
        loadingAnimation.innerHTML = `
            <div class="loading-dots">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
            <p>改善内容を適用中...</p>
        `;
        
        // テキストエリアのコンテナを取得
        const scriptContainer = scriptTextarea.parentElement;
        
        // テキストエリアを非表示にして、アニメーションを表示
        scriptTextarea.style.display = 'none';
        scriptContainer.appendChild(loadingAnimation);
        
        // 動画時間を取得（分単位）
        const durationInput = document.getElementById('duration-input');
        const durationMinutes = parseInt(durationInput.value) || 3;
        
        fetch('/api/bedrock-scripts/apply-improvement', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                duration_minutes: durationMinutes // 動画時間パラメータを追加
            })
        })
        .then(response => response.json())
        .then(data => {
            // アニメーションを削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }
            
            // テキストエリアを再び表示
            scriptTextarea.style.display = '';
            
            if (data.success) {
                // スクリプトを更新
                scripts[currentChapterIndex] = data.script;
                
                // 警告があるかチェック
                if (data.warning) {
                    console.log("警告:", data.warning);
                }
                
                // UI更新
                displayScript(data.script);
                renderChapterList();
                
                // ハイライトを解除
                applyImprovementButton.classList.remove('highlight');
                applyImprovementButton.classList.add('hidden');
                
                // 成功メッセージ
                showToast('改善された台本を適用しました。DynamoDBに保存されました。', 'success');
            } else {
                showToast('改善の適用に失敗しました: ' + data.error, 'error');
                // 失敗した場合は元に戻す
                scriptTextarea.value = scripts[currentChapterIndex].improved_script || scripts[currentChapterIndex].script_content;
            }
        })
        .catch(error => {
            console.error('Error:', error);

            // アニメーションを削除
            const loadingAnimation = document.querySelector('.script-loading-animation');
            if (loadingAnimation) {
                loadingAnimation.remove();
            }

            // テキストエリアを再び表示
            scriptTextarea.style.display = '';
            scriptTextarea.value = scripts[currentChapterIndex].improved_script || scripts[currentChapterIndex].script_content;
            showToast('改善適用処理中にエラーが発生しました。', 'error');
        });
    });
    
    // showScriptGenerationOption関数は不要になりました
    // 元のコードは削除または無効化します
    /*
    const originalStartAnalysis = startAnalysis;
    startAnalysis = function() {
        originalStartAnalysis();
        
        // ページ読み込み完了後に台本生成ボタンを表示判定
        setTimeout(() => {
            showScriptGenerationOption();
        }, 1000);
    };
    */
});