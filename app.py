import os
import re
import json
import requests
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from pypdf import PdfReader

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_FILE = "paper_database.json"

# DeepSeek API配置（从环境变量读取）
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# ----------------------
# 加载文献库
# ----------------------
def load_papers():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

# ----------------------
# 保存文献库
# ----------------------
def save_papers(papers):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)

# ----------------------
# 用DeepSeek AI提取文献信息
# ----------------------
def extract_info_by_ai(text):
    try:
        prompt = f"""请从以下学术论文首页文本中提取信息，以JSON格式返回：
{{
    "title": "论文标题",
    "author": "第一作者姓名",
    "year": "发表年份"
}}

注意：
1. 标题通常是最大字号、最显眼的文字，不是"Abstract"、"Introduction"等
2. 作者名通常在标题下方，可能是英文名或中文名
3. 年份可能在作者信息中，或从期刊信息推断

文本内容：
{text[:3000]}"""

        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if json_match:
                info = json.loads(json_match.group())
                return info
    except Exception as e:
        print(f"AI提取失败: {e}")
    return None

# ----------------------
# 从PDF提取首页文本
# ----------------------
def extract_text_from_pdf(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        if len(reader.pages) > 0:
            return reader.pages[0].extract_text()
    except:
        pass
    return ""

# ----------------------
# 从PDF提取完整信息（使用AI）
# ----------------------
def extract_info_from_pdf(pdf_path, filename):
    try:
        text = extract_text_from_pdf(pdf_path)
        ai_info = extract_info_by_ai(text)

        if ai_info:
            if ai_info.get('year') == '未知' or not ai_info.get('year'):
                year_match = re.search(r'(20\d{2})', filename)
                ai_info['year'] = year_match.group(1) if year_match else "未知"

            return {
                "title": ai_info.get('title', '未知'),
                "author": ai_info.get('author', '未知'),
                "year": ai_info.get('year', '未知'),
                "filename": filename
            }

        # AI失败，用传统方法
        reader = PdfReader(pdf_path)
        info = reader.metadata
        title = info.title if info and info.title else None
        author = info.author if info and info.author else None

        year_match = re.search(r'(20\d{2})', filename)
        year = year_match.group(1) if year_match else "未知"

        if not title and len(reader.pages) > 0:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            for line in lines[:15]:
                if len(line) > 15 and not line.lower().startswith(('abstract', 'keywords', 'introduction', 'http', 'www', 'doi')):
                    title = line
                    break

        if not author:
            author_match = re.match(r'^[\d\s]*([A-Za-z一-鿿]+)', filename)
            author = author_match.group(1) if author_match else "未知"

        return {
            "title": title or filename.replace('.pdf', ''),
            "author": author,
            "year": year,
            "filename": filename
        }
    except Exception as e:
        return None

# ----------------------
# 查重逻辑（模糊匹配）
# ----------------------
def is_duplicate(new_title, paper_list, threshold=65):
    if not new_title:
        return False, None
    new_clean = re.sub(r'[^\w]', '', new_title.lower()).strip()
    for paper in paper_list:
        p_title = paper["title"]
        p_clean = re.sub(r'[^\w]', '', p_title.lower()).strip()
        if new_clean in p_clean or p_clean in new_clean:
            return True, paper
        common = set(new_clean) & set(p_clean)
        rate = len(common) / max(len(new_clean), len(p_clean)) * 100
        if rate >= threshold:
            return True, paper
    return False, None

# ----------------------
# 网页界面
# ----------------------
@app.route('/')
def index():
    return '''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>组会文献查重系统</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}
body{
    max-width:950px;
    margin:0 auto;
    font-family:'Nunito',-apple-system,sans-serif;
    padding:30px 20px;
    background:linear-gradient(135deg,#fce4ec 0%,#e3f2fd 100%);
    min-height:100vh
}
.header{
    text-align:center;
    margin-bottom:30px
}
.header h1{
    color:#e91e63;
    font-size:2.2em;
    margin:0;
    display:flex;
    align-items:center;
    justify-content:center;
    gap:10px
}
.header p{
    color:#666;
    margin-top:8px;
    font-size:1.1em
}
.count-badge{
    display:inline-block;
    background:linear-gradient(135deg,#e91e63,#f48fb1);
    color:white;
    padding:8px 20px;
    border-radius:50px;
    font-weight:700;
    margin-top:15px;
    box-shadow:0 4px 15px rgba(233,30,99,0.3)
}
.upload-section{
    background:white;
    border-radius:20px;
    padding:35px;
    margin:25px 0;
    box-shadow:0 10px 40px rgba(0,0,0,0.1);
    text-align:center
}
.upload-area{
    border:3px dashed #f48fb1;
    padding:40px;
    border-radius:15px;
    background:#fce4ec20;
    transition:all 0.3s
}
.upload-area:hover{
    border-color:#e91e63;
    background:#fce4ec40
}
.upload-icon{
    font-size:50px;
    margin-bottom:15px
}
.file-input{
    display:none
}
.file-label{
    display:inline-block;
    padding:12px 30px;
    background:linear-gradient(135deg,#e91e63,#f48fb1);
    color:white;
    border-radius:50px;
    cursor:pointer;
    font-weight:600;
    transition:transform 0.2s,box-shadow 0.2s;
    box-shadow:0 4px 15px rgba(233,30,99,0.3)
}
.file-label:hover{
    transform:translateY(-2px);
    box-shadow:0 6px 20px rgba(233,30,99,0.4)
}
.file-name{
    margin-top:15px;
    color:#666;
    font-size:0.95em
}
.check-btn{
    margin-top:20px;
    padding:14px 40px;
    background:linear-gradient(135deg,#7c4dff,#b388ff);
    color:white;
    border:none;
    border-radius:50px;
    cursor:pointer;
    font-weight:700;
    font-size:1.1em;
    transition:transform 0.2s,box-shadow 0.2s;
    box-shadow:0 4px 15px rgba(124,77,255,0.3)
}
.check-btn:hover{
    transform:translateY(-2px);
    box-shadow:0 6px 20px rgba(124,77,255,0.4)
}
.result-box{
    margin-top:25px;
    padding:25px;
    border-radius:15px;
    display:none;
    animation:popIn 0.3s ease
}
@keyframes popIn{
    from{opacity:0;transform:scale(0.95)}
    to{opacity:1;transform:scale(1)}
}
.result-duplicate{
    background:linear-gradient(135deg,#ffebee,#ffcdd2);
    border-left:5px solid #e53935
}
.result-new{
    background:linear-gradient(135deg,#e8f5e9,#c8e6c9);
    border-left:5px solid #43a047
}
.result-title{
    font-size:1.2em;
    font-weight:700;
    margin-bottom:15px
}
.result-content{
    color:#555;
    line-height:1.8
}
.paper-match{
    background:white;
    padding:15px;
    border-radius:10px;
    margin-top:15px;
    box-shadow:0 2px 10px rgba(0,0,0,0.05)
}
.add-btn{
    margin-top:15px;
    padding:12px 35px;
    background:linear-gradient(135deg,#00c853,#69f0ae);
    color:white;
    border:none;
    border-radius:50px;
    cursor:pointer;
    font-weight:700;
    transition:transform 0.2s,box-shadow 0.2s;
    box-shadow:0 4px 15px rgba(0,200,83,0.3)
}
.add-btn:hover{
    transform:translateY(-2px);
    box-shadow:0 6px 20px rgba(0,200,83,0.4)
}
.paper-list-section{
    margin-top:40px;
    background:white;
    border-radius:20px;
    padding:30px;
    box-shadow:0 10px 40px rgba(0,0,0,0.1)
}
.paper-list-header{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
    margin-bottom:20px
}
.paper-list-header h2{
    color:#e91e63;
    margin:0;
    font-size:1.5em
}
.add-paper-btn{
    padding:10px 20px;
    background:linear-gradient(135deg,#00c853,#69f0ae);
    color:white;
    border:none;
    border-radius:50px;
    cursor:pointer;
    font-weight:600;
    font-size:0.9em
}
.search-input{
    width:100%;
    max-width:400px;
    padding:12px 20px;
    border:2px solid #fce4ec;
    border-radius:50px;
    font-size:1em;
    transition:border-color 0.3s;
    margin-bottom:20px
}
.search-input:focus{
    outline:none;
    border-color:#e91e63
}
.paper-item{
    background:linear-gradient(135deg,#fce4ec10,#e3f2fd10);
    padding:15px 20px;
    margin:12px 0;
    border-radius:12px;
    border-left:4px solid #e91e63;
    transition:transform 0.2s,box-shadow 0.2s;
    position:relative
}
.paper-item:hover{
    transform:translateX(5px);
    box-shadow:0 4px 15px rgba(233,30,99,0.1)
}
.paper-title{
    font-weight:700;
    color:#333;
    font-size:1.05em;
    padding-right:80px
}
.paper-meta{
    color:#888;
    font-size:0.9em;
    margin-top:6px
}
.paper-actions{
    position:absolute;
    right:15px;
    top:50%;
    transform:translateY(-50%);
    display:flex;
    gap:8px
}
.edit-btn,.delete-btn{
    padding:6px 12px;
    border:none;
    border-radius:20px;
    cursor:pointer;
    font-size:0.85em;
    font-weight:600
}
.edit-btn{
    background:#e3f2fd;
    color:#1976d2
}
.edit-btn:hover{
    background:#bbdefb
}
.delete-btn{
    background:#ffebee;
    color:#e53935
}
.delete-btn:hover{
    background:#ffcdd2
}
.empty-msg{
    text-align:center;
    color:#aaa;
    padding:30px
}
.loading{
    text-align:center;
    padding:20px;
    color:#e91e63
}
/* 编辑弹窗 */
.modal{
    display:none;
    position:fixed;
    top:0;
    left:0;
    width:100%;
    height:100%;
    background:rgba(0,0,0,0.5);
    z-index:1000;
    justify-content:center;
    align-items:center
}
.modal.show{
    display:flex
}
.modal-content{
    background:white;
    padding:30px;
    border-radius:20px;
    width:90%;
    max-width:500px;
    box-shadow:0 20px 60px rgba(0,0,0,0.3)
}
.modal-title{
    color:#e91e63;
    margin:0 0 20px 0;
    font-size:1.3em
}
.modal-input{
    width:100%;
    padding:12px 15px;
    border:2px solid #fce4ec;
    border-radius:10px;
    font-size:1em;
    margin-bottom:15px
}
.modal-input:focus{
    outline:none;
    border-color:#e91e63
}
.modal-label{
    display:block;
    margin-bottom:5px;
    color:#666;
    font-weight:600
}
.modal-btns{
    display:flex;
    gap:10px;
    justify-content:flex-end;
    margin-top:20px
}
.modal-btn{
    padding:10px 25px;
    border:none;
    border-radius:50px;
    cursor:pointer;
    font-weight:600
}
.modal-cancel{
    background:#f5f5f5;
    color:#666
}
.modal-save{
    background:linear-gradient(135deg,#e91e63,#f48fb1);
    color:white
}
</style>
</head>
<body>
<div class="header">
    <h1><span>📚</span> 组会文献查重系统</h1>
    <p>上传PDF，一键查重，告别重复分享</p>
    <div class="count-badge">已收录 <span id="count">...</span> 篇文献</div>
</div>

<div class="upload-section">
    <div class="upload-area">
        <div class="upload-icon">📄</div>
        <input type="file" id="pdfFile" class="file-input" accept=".pdf">
        <label for="pdfFile" class="file-label">选择PDF文件</label>
        <div class="file-name" id="fileName">未选择文件</div>
    </div>
    <button class="check-btn" onclick="uploadAndCheck()">上传并查重</button>
</div>

<div id="result" class="result-box"></div>

<div class="paper-list-section">
    <div class="paper-list-header">
        <h2>📖 已分享文献汇总</h2>
        <button class="add-paper-btn" onclick="showAddModal()">+ 手动添加</button>
    </div>
    <input type="text" id="searchInput" class="search-input" placeholder="搜索标题、作者或年份..." oninput="filterPapers()">
    <div id="paperList" class="loading">加载中...</div>
</div>

<!-- 编辑弹窗 -->
<div id="editModal" class="modal">
    <div class="modal-content">
        <h3 class="modal-title">编辑文献信息</h3>
        <label class="modal-label">标题</label>
        <input type="text" id="editTitle" class="modal-input">
        <label class="modal-label">作者</label>
        <input type="text" id="editAuthor" class="modal-input">
        <label class="modal-label">年份</label>
        <input type="text" id="editYear" class="modal-input">
        <input type="hidden" id="editIndex">
        <div class="modal-btns">
            <button class="modal-btn modal-cancel" onclick="closeModal()">取消</button>
            <button class="modal-btn modal-save" onclick="saveEdit()">保存</button>
        </div>
    </div>
</div>

<!-- 添加弹窗 -->
<div id="addModal" class="modal">
    <div class="modal-content">
        <h3 class="modal-title">手动添加文献</h3>
        <label class="modal-label">标题</label>
        <input type="text" id="addTitle" class="modal-input" placeholder="请输入论文标题">
        <label class="modal-label">作者</label>
        <input type="text" id="addAuthor" class="modal-input" placeholder="请输入作者">
        <label class="modal-label">年份</label>
        <input type="text" id="addYear" class="modal-input" placeholder="请输入年份">
        <div class="modal-btns">
            <button class="modal-btn modal-cancel" onclick="closeAddModal()">取消</button>
            <button class="modal-btn modal-save" onclick="manualAdd()">添加</button>
        </div>
    </div>
</div>

<script>
let allPapers = [];
let lastCheckData = null;

document.getElementById('pdfFile').addEventListener('change', function(e){
    let name = e.target.files[0] ? e.target.files[0].name : '未选择文件';
    document.getElementById('fileName').textContent = name;
});

async function loadCount(){
    let r = await fetch('/count');
    let d = await r.json();
    document.getElementById('count').textContent = d.count;
}

async function loadPapers(){
    let r = await fetch('/papers');
    allPapers = await r.json();
    renderPapers(allPapers);
}

function renderPapers(papers){
    let html = '';
    if(papers.length === 0){
        html = '<div class="empty-msg">暂无文献记录</div>';
    } else {
        papers.forEach((p, i) => {
            html += `<div class="paper-item">
                <div class="paper-title">${i+1}. ${p.title}</div>
                <div class="paper-meta">作者：${p.author} | 年份：${p.year}</div>
                <div class="paper-actions">
                    <button class="edit-btn" onclick="editPaper(${i})">编辑</button>
                    <button class="delete-btn" onclick="deletePaper(${i})">删除</button>
                </div>
            </div>`;
        });
    }
    document.getElementById('paperList').innerHTML = html;
}

function filterPapers(){
    let keyword = document.getElementById('searchInput').value.toLowerCase();
    let filtered = allPapers.filter(p =>
        p.title.toLowerCase().includes(keyword) ||
        p.author.toLowerCase().includes(keyword) ||
        p.year.includes(keyword)
    );
    renderPapers(filtered);
}

async function uploadAndCheck(){
    let file = document.getElementById('pdfFile').files[0];
    if(!file){alert('请先选择PDF文件');return}
    let form = new FormData();
    form.append('pdf', file);

    let dom = document.getElementById('result');
    dom.className = 'result-box';
    dom.style.display = 'block';
    dom.innerHTML = '<div class="loading">正在用AI识别文献信息...</div>';

    let res = await fetch('/upload-check', {method:'POST', body:form});
    let data = await res.json();
    lastCheckData = data;

    if(data.duplicate){
        dom.className = 'result-box result-duplicate';
        dom.innerHTML = `
            <div class="result-title">❌ 这篇文献已经分享过啦</div>
            <div class="result-content">
                <strong>识别标题：</strong>${data.title}<br>
                <strong>识别作者：</strong>${data.full_info?.author || '未知'}<br>
                <strong>识别年份：</strong>${data.full_info?.year || '未知'}
                <div class="paper-match">
                    <strong>已存在记录：</strong><br>
                    ${data.paper.title}<br>
                    <small>作者：${data.paper.author} | 年份：${data.paper.year}</small>
                </div>
            </div>`;
    } else {
        dom.className = 'result-box result-new';
        dom.innerHTML = `
            <div class="result-title">✅ 这是一篇新文献，可以分享</div>
            <div class="result-content">
                <strong>识别标题：</strong>${data.title}<br>
                <strong>识别作者：</strong>${data.full_info?.author || '未知'}<br>
                <strong>识别年份：</strong>${data.full_info?.year || '未知'}
                <br><br>
                <button class="add-btn" onclick="addToDatabase()">添加到数据库</button>
            </div>`;
    }
}

async function addToDatabase(){
    if(!lastCheckData || !lastCheckData.full_info){
        alert('请先上传并查重');
        return;
    }

    let res = await fetch('/add-paper', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(lastCheckData.full_info)
    });
    let data = await res.json();

    if(data.success){
        let dom = document.getElementById('result');
        dom.innerHTML = '<div class="result-title">🎉 已成功添加到数据库</div>';
        loadCount();
        loadPapers();
        lastCheckData = null;
    } else {
        alert('添加失败：' + (data.error || '未知错误'));
    }
}

function editPaper(index){
    let paper = allPapers[index];
    document.getElementById('editTitle').value = paper.title;
    document.getElementById('editAuthor').value = paper.author;
    document.getElementById('editYear').value = paper.year;
    document.getElementById('editIndex').value = index;
    document.getElementById('editModal').classList.add('show');
}

function closeModal(){
    document.getElementById('editModal').classList.remove('show');
}

async function saveEdit(){
    let index = parseInt(document.getElementById('editIndex').value);
    let updatedPaper = {
        title: document.getElementById('editTitle').value,
        author: document.getElementById('editAuthor').value,
        year: document.getElementById('editYear').value,
        filename: allPapers[index].filename || ''
    };

    let res = await fetch('/update-paper', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({index: index, paper: updatedPaper})
    });
    let data = await res.json();

    if(data.success){
        closeModal();
        loadPapers();
        loadCount();
    } else {
        alert('保存失败');
    }
}

async function deletePaper(index){
    if(!confirm('确定要删除这篇文献吗？')) return;

    let res = await fetch('/delete-paper', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({index: index})
    });
    let data = await res.json();

    if(data.success){
        loadPapers();
        loadCount();
    } else {
        alert('删除失败');
    }
}

function showAddModal(){
    document.getElementById('addTitle').value = '';
    document.getElementById('addAuthor').value = '';
    document.getElementById('addYear').value = '';
    document.getElementById('addModal').classList.add('show');
}

function closeAddModal(){
    document.getElementById('addModal').classList.remove('show');
}

async function manualAdd(){
    let paper = {
        title: document.getElementById('addTitle').value,
        author: document.getElementById('addAuthor').value,
        year: document.getElementById('addYear').value,
        filename: ''
    };

    if(!paper.title){
        alert('请输入标题');
        return;
    }

    let res = await fetch('/add-paper', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(paper)
    });
    let data = await res.json();

    if(data.success){
        closeAddModal();
        loadPapers();
        loadCount();
    } else {
        alert('添加失败：' + (data.error || '未知错误'));
    }
}

loadCount();
loadPapers();
</script>
</body>
</html>
'''

# ----------------------
# 接口：文献数量
# ----------------------
@app.route('/count')
def count():
    return jsonify(count=len(load_papers()))

# ----------------------
# 接口：文献列表
# ----------------------
@app.route('/papers')
def papers():
    return jsonify(load_papers())

# ----------------------
# 接口：上传PDF + 查重
# ----------------------
@app.route('/upload-check', methods=['POST'])
def upload_check():
    if 'pdf' not in request.files:
        return jsonify({"error": "no file"}), 400

    file = request.files['pdf']
    if file.filename == '':
        return jsonify({"error": "no selected file"}), 400

    filename = secure_filename(file.filename)
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)

    full_info = extract_info_from_pdf(path, filename)
    title = full_info['title'] if full_info else "无法识别标题"

    papers = load_papers()
    dup, paper = is_duplicate(title, papers)

    os.remove(path)

    return jsonify({
        "title": title,
        "duplicate": dup,
        "paper": paper,
        "full_info": full_info
    })

# ----------------------
# 接口：添加文献
# ----------------------
@app.route('/add-paper', methods=['POST'])
def add_paper():
    data = request.json
    if not data or 'title' not in data:
        return jsonify({"error": "missing data"}), 400

    papers = load_papers()
    dup, _ = is_duplicate(data['title'], papers)
    if dup:
        return jsonify({"error": "already exists"}), 400

    new_paper = {
        "title": data.get('title', '未知'),
        "author": data.get('author', '未知'),
        "year": data.get('year', '未知'),
        "filename": data.get('filename', '')
    }
    papers.append(new_paper)
    save_papers(papers)

    return jsonify({"success": True, "paper": new_paper})

# ----------------------
# 接口：更新文献
# ----------------------
@app.route('/update-paper', methods=['POST'])
def update_paper():
    data = request.json
    index = data.get('index')
    paper = data.get('paper')

    if index is None or not paper:
        return jsonify({"error": "missing data"}), 400

    papers = load_papers()
    if index < 0 or index >= len(papers):
        return jsonify({"error": "invalid index"}), 400

    papers[index] = paper
    save_papers(papers)

    return jsonify({"success": True})

# ----------------------
# 接口：删除文献
# ----------------------
@app.route('/delete-paper', methods=['POST'])
def delete_paper():
    data = request.json
    index = data.get('index')

    if index is None:
        return jsonify({"error": "missing index"}), 400

    papers = load_papers()
    if index < 0 or index >= len(papers):
        return jsonify({"error": "invalid index"}), 400

    papers.pop(index)
    save_papers(papers)

    return jsonify({"success": True})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    print("文献查重系统已启动")
    app.run(host='0.0.0.0', port=port)
