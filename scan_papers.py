import os
import re
import json
from pypdf import PdfReader

DB_FILE = "paper_database.json"

def extract_info_from_pdf(pdf_path):
    """从PDF提取标题、作者、年份等信息"""
    try:
        reader = PdfReader(pdf_path)
        info = reader.metadata

        # 从元数据获取信息
        title = info.title if info and info.title else None
        author = info.author if info and info.author else None

        # 从文件名提取年份
        filename = os.path.basename(pdf_path)
        year_match = re.search(r'(20\d{2})', filename)
        year = year_match.group(1) if year_match else None

        # 如果元数据没有标题，从第一页文本提取
        if not title and len(reader.pages) > 0:
            text = reader.pages[0].extract_text()[:3000].strip()
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            for line in lines[:15]:
                # 跳过太短的行和常见的非标题行
                if len(line) > 15 and not line.lower().startswith(('abstract', 'keywords', 'introduction', 'http', 'www', 'doi', 'vol.', 'journal')):
                    title = line
                    break

        # 如果没有作者信息，尝试从文件名提取
        if not author:
            # 常见格式: "2025 Hsu等 MS.pdf" 或 "Zhou 等 - 2025 - ..."
            author_match = re.match(r'^[\d\s]*([A-Za-z\u4e00-\u9fff]+)', filename)
            if author_match:
                author = author_match.group(1)

        return {
            "title": title or filename.replace('.pdf', ''),
            "author": author or "未知",
            "year": year or "未知",
            "journal": "未知",
            "filename": filename
        }
    except Exception as e:
        print(f"处理 {pdf_path} 时出错: {e}")
        return None

def scan_papers(directory="."):
    """扫描目录下所有PDF文件"""
    papers = []

    for filename in os.listdir(directory):
        if filename.lower().endswith('.pdf'):
            pdf_path = os.path.join(directory, filename)
            try:
                print(f"正在处理: {filename.encode('utf-8', errors='replace').decode('utf-8')}")
            except:
                print(f"正在处理: {filename}")

            info = extract_info_from_pdf(pdf_path)
            if info:
                papers.append(info)
                try:
                    print(f"  -> 标题: {info['title'][:50]}...")
                    print(f"  -> 作者: {info['author']}, 年份: {info['year']}")
                except:
                    pass

    return papers

def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print("PDF文献扫描工具")
    print("=" * 50)

    papers = scan_papers()

    # 保存到JSON文件
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 50)
    print(f"扫描完成！共收录 {len(papers)} 篇文献")
    print(f"数据库已保存到: {DB_FILE}")
    print("=" * 50)

if __name__ == "__main__":
    main()
