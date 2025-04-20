document.addEventListener('DOMContentLoaded', () => {
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
    const feedbackList = document.getElementById('feedback-list');
    const feedbackTextarea = document.getElementById('feedback-textarea');
    const analysisResult = document.getElementById('analysis-result');
    const analyzeScriptButton = document.getElementById('analyze-script-button');
    const approveScriptButton = document.getElementById('approve-script-button');
    const rejectScriptButton = document.getElementById('reject-script-button');
    const applyImprovementButton = document.getElementById('apply-improvement-button');

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
        
        // POSTリクエスト送信し、Server-Sent Events (SSE) で結果を受信
        // 解析タイプに基づいて異なるエンドポイントを使用
        const apiEndpoint = currentAnalyzeType === 'chapters' ? '/api/analyze/chapters' : '/api/analyze';
        fetch(apiEndpoint, {
            method: 'POST',
            body: formData
        }).then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error ${response.status}`);
            }
            
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            
            // ストリーミングレスポンスの処理
            function readStream() {
                return reader.read().then(({ done, value }) => {
                    if (done) {
                        loading.classList.add('hidden');
                        analyzeButton.disabled = false;
                        return;
                    }
                    
                    buffer += decoder.decode(value, { stream: true });
                    
                    // Server-Sent Eventsの形式（data: {...}\n\n）でパース
                    const lines = buffer.split('\n\n');
                    buffer = lines.pop() || '';
                    
                    for (const line of lines) {
                        if (line.trim() === '') continue;
                        
                        try {
                            const dataLine = line.replace(/^data: /, '');
                            const data = JSON.parse(dataLine);
                            
                            if (data.text) {
                                // テキストの追加（白空白とテキストを保持）
                                const textNode = document.createTextNode(data.text);
                                resultsContent.appendChild(textNode);
                                
                                // スクロール位置を最下部に調整
                                resultsContent.scrollTop = resultsContent.scrollHeight;
                            }
                            
                            if (data.error) {
                                // エラー表示
                                resultsContent.innerHTML += `<div class="error">Error: ${data.error}</div>`;
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
                        } catch (e) {
                            console.error('SSEパースエラー:', e, line);
                        }
                    }
                    
                    return readStream();
                });
            }
            
            return readStream();
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
        
        // 最初のチャプターを選択
        if (chapters.length > 0) {
            selectChapter(0);
        }
        
        // 解析結果セクションを隠す（任意）
        // resultsSection.classList.add('hidden');
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
        
        // スクリプトデータがあれば表示、なければ生成
        const script = scripts[index];
        if (script) {
            displayScript(script);
        } else {
            generateScript(index);
        }
    }
    
    // スクリプトデータを表示
    function displayScript(script) {
        scriptTextarea.value = script.script_content;
        
        // ステータス表示
        updateScriptStatus(script.status);
        
        // フィードバック表示
        // フィードバックコンテナは常に表示
        feedbackContainer.classList.remove('hidden');
        feedbackList.innerHTML = '';
        
        // 過去のフィードバック履歴を表示
        if (script.feedback && script.feedback.length > 0) {
            // 過去のフィードバック履歴がある場合
            script.feedback.forEach(feedback => {
                const feedbackItem = document.createElement('div');
                feedbackItem.className = 'script-feedback';
                feedbackItem.textContent = feedback;
                feedbackList.appendChild(feedbackItem);
            });
        } else {
            // フィードバック履歴がない場合
            feedbackList.innerHTML = '<p>まだフィードバックはありません</p>';
        }
        
        // フィードバック入力欄をクリア
        feedbackTextarea.value = '';
        
        // 分析結果の表示
        if (script.analysis) {
            analysisResult.textContent = script.analysis;
            analysisResult.classList.remove('hidden');
        } else {
            analysisResult.classList.add('hidden');
        }
        
        // 改善適用ボタンの表示/非表示
        if (script.improved_script) {
            // 改善された台本がある場合は表示
            applyImprovementButton.classList.remove('hidden');
            console.log("改善適用ボタンを表示します");
        } else {
            // 改善された台本がない場合は非表示
            applyImprovementButton.classList.add('hidden');
            console.log("改善適用ボタンを非表示にします");
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
        }
    }
    
    // 台本を生成する
    function generateScript(index) {
        // ローディング表示
        scriptTextarea.value = '台本を生成中...';
        scriptTextarea.disabled = true;
        
        fetch('/api/bedrock-scripts/generate-script', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: index,
                chapters: chapters
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // 生成された台本を保存
                scripts[index] = data.script;
                
                // 表示を更新
                scriptTextarea.value = data.script.script_content;
                scriptTextarea.disabled = false;
                
                // ステータス表示
                updateScriptStatus(data.script.status);
                
                // チャプターリストの表示を更新
                renderChapterList();
            } else {
                scriptTextarea.value = '台本の生成に失敗しました: ' + data.error;
                scriptTextarea.disabled = false;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            scriptTextarea.value = '台本の生成中にエラーが発生しました。';
            scriptTextarea.disabled = false;
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
        
        fetch('/api/bedrock-scripts/analyze-script', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                script_content: scriptContent
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
        
        // フィードバックを取得（空でも可）
        const feedbackText = feedbackTextarea.value || '承認しました。';
        
        fetch('/api/bedrock-scripts/submit-feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                feedback: feedbackText,
                is_approved: true
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // スクリプトの状態を更新
                scripts[currentChapterIndex].status = 'approved';
                
                // UI更新
                updateScriptStatus('approved');
                renderChapterList();
                
                // 次のチャプターがあれば選択
                if (currentChapterIndex + 1 < chapters.length) {
                    selectChapter(currentChapterIndex + 1);
                }
            } else {
                alert('承認に失敗しました: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('承認処理中にエラーが発生しました。');
        });
    });
    
    // 修正依頼ボタン
    rejectScriptButton.addEventListener('click', () => {
        if (currentChapterIndex < 0) return;
        
        // フィードバックコンテナを表示する
        feedbackContainer.classList.remove('hidden');
        // フォーカスを当てる
        feedbackTextarea.focus();
        
        // 既にフィードバックが入力されているか確認
        const feedbackText = feedbackTextarea.value;
        if (!feedbackText) {
            // フィードバックが入力されていない場合は入力を促すだけ
            alert('フィードバックを入力してから再度「修正依頼」ボタンをクリックしてください。');
            return;
        }
        
        fetch('/api/bedrock-scripts/submit-feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex,
                feedback: feedbackText,
                is_approved: false
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // スクリプトの状態を更新
                scripts[currentChapterIndex].status = 'rejected';
                
                // フィードバックリストに追加
                if (!scripts[currentChapterIndex].feedback) {
                    scripts[currentChapterIndex].feedback = [];
                }
                scripts[currentChapterIndex].feedback.push(feedbackText);
                
                // 改善された台本があれば保存
                if (data.improved_script) {
                    scripts[currentChapterIndex].improved_script = data.improved_script;
                    
                    // 成功メッセージとともに改善適用ボタンを表示して目立たせる
                    alert('フィードバックを受け付けました。改善された台本が生成されました。「改善を適用」ボタンをクリックして確認してください。');
                    applyImprovementButton.classList.remove('hidden');
                    applyImprovementButton.classList.add('highlight');
                } else {
                    alert('フィードバックを受け付けましたが、台本の改善に失敗しました。');
                }
                
                // UI更新
                updateScriptStatus('rejected');
                renderChapterList();
                displayScript(scripts[currentChapterIndex]);
                
                // フィードバック入力欄をクリア
                feedbackTextarea.value = '';
            } else {
                alert('修正依頼に失敗しました: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('修正依頼処理中にエラーが発生しました。');
        });
    });
    
    // 改善適用ボタン
    applyImprovementButton.addEventListener('click', () => {
        if (currentChapterIndex < 0) return;
        
        fetch('/api/bedrock-scripts/apply-improvement', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chapter_index: currentChapterIndex
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // スクリプトを更新
                scripts[currentChapterIndex] = data.script;
                
                // UI更新
                displayScript(data.script);
                renderChapterList();
                
                // ハイライトを解除
                applyImprovementButton.classList.remove('highlight');
                
                // 成功メッセージ
                alert('改善された台本を適用しました。内容を確認してください。');
            } else {
                alert('改善の適用に失敗しました: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('改善適用処理中にエラーが発生しました。');
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