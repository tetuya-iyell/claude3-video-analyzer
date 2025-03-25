document.addEventListener('DOMContentLoaded', () => {
    // DOM要素の参照
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const filename = document.getElementById('filename');
    const changeFileButton = document.getElementById('change-file');
    const promptTextarea = document.getElementById('prompt');
    const analyzeButton = document.getElementById('analyze-button');
    const loading = document.getElementById('loading');
    const resultsSection = document.getElementById('results-section');
    const resultsContent = document.getElementById('results-content');
    const copyButton = document.getElementById('copy-button');
    const newAnalysisButton = document.getElementById('new-analysis');

    // 選択された動画ファイル
    let selectedFile = null;

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
        if (!selectedFile) return;

        // 解析開始前の表示更新
        analyzeButton.disabled = true;
        loading.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        resultsContent.innerHTML = '';

        // FormDataの作成
        const formData = new FormData();
        formData.append('video', selectedFile);
        formData.append('prompt', promptTextarea.value);
        
        // 解析セクションを表示
        resultsSection.classList.remove('hidden');
        resultsContent.innerHTML = '';
        
        // POSTリクエスト送信し、Server-Sent Events (SSE) で結果を受信
        fetch('/api/analyze', {
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
                                // テキストの追加
                                const textNode = document.createTextNode(data.text);
                                resultsContent.appendChild(textNode);
                            }
                            
                            if (data.error) {
                                // エラー表示
                                resultsContent.innerHTML += `<div class="error">Error: ${data.error}</div>`;
                            }
                            
                            if (data.complete) {
                                // 完了処理
                                loading.classList.add('hidden');
                                analyzeButton.disabled = false;
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
    });
});