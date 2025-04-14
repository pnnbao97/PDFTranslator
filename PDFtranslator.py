from typing import Optional, List, Tuple
import sqlite3
import pdfplumber
import asyncio
from asyncio import Semaphore
import logging 
import os
from semantic_kernel import Kernel
from semantic_kernel.contents import ChatHistory
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.agents import ChatCompletionAgent
from openai import AsyncOpenAI
from pyfiglet import Figlet
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
import yaml
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TaskProgressColumn,
    MofNCompleteColumn
)

logging.basicConfig(level=logging.WARNING)

def load_config(config_path: str = "config.yaml") -> dict:
    """Đọc file cấu hình YAML"""
    try:
        with open(config_path, "r") as file:
            config = yaml.safe_load(file)
        return config
    except FileNotFoundError:
        logging.error(f"File cấu hình {config_path} không tồn tại")
        raise
    except yaml.YAMLError as e:
        logging.error(f"Lỗi khi đọc file cấu hình: {str(e)}")
        raise

# Đọc cấu hình ngay khi khởi động
CONFIG = load_config()
OPTIMUS_KEY = os.getenv("OPTIMUS_KEY")

def build_prompt(style: str, domain: Optional[str], translation_type: str) -> str:
    """Xây dựng prompt dựa trên lựa chọn người dùng"""
    style_map = {
        "formal": "phong cách học thuật, trang trọng",
        "informal": "phong cách tự nhiên, thân mật"
    }

    type_map = {
        "literal": "dịch sát nghĩa từng từ",
        "summary": "dịch dạng tóm tắt, ngắn gọn",
        "adaptive": "dịch linh hoạt theo ngữ cảnh"
    }

    domain_section = f"\n- Chuyên ngành: {domain}. Giữ nguyên thuật ngữ chuyên môn kèm chú thích tiếng Việt." if domain else ""

    return f"""
**Bạn là chuyên gia dịch thuật chuyên nghiệp. Hãy:**

1. Dịch nội dung tiếng Anh sang tiếng Việt theo kiểu {style_map[style]}.
2. **KHÔNG DỊCH các đoạn code** - Giữ nguyên và bao quanh bằng markdown ```.
3. Áp dụng định dạng markdown phù hợp{domain_section}
4. Phương pháp dịch: {type_map[translation_type]}
5. Đảm bảo tính mạch lạc và ngắt đoạn hợp lý. Dịch đầy đủ nội dung gốc, không lược bỏ thông tin (trừ khi chọn kiểu dịch dạng tóm tắt).
"- Độ dài bản dịch không vượt quá 120% độ dài bản gốc."
6. Sửa lỗi văn bản gốc nếu phát hiện và ghi chú
7. Kiểm tra tính nhất quán thuật ngữ
    """

console = Console()

def get_user_input() -> dict:
    # Hiển thị tiêu đề
    f = Figlet(font="slant")
    console.print(f"[bold cyan]{f.renderText('PDF TRANSLATOR')}[/bold cyan]", justify="center")
    console.print(Panel("[bold green]⚡ Phiên bản 1.0 - Dịch thuật thông minh với AI[/bold green]", width=80), justify="center")
    console.print(Panel("[bold green]⚡ Dr.PUMA - pnnbao@gmail.com", width=80), justify="center")

    inputs = {
        "file_path": Prompt.ask("📁 [bold]Đường dẫn file PDF[/bold]", default="input.pdf"),
        "start_page": IntPrompt.ask("🔖 [bold]Trang bắt đầu[/bold]", default=1),
        "end_page": IntPrompt.ask("🏁 [bold]Trang kết thúc[/bold]", default=1),
        "style": None,
        "domain": None,
        "translation_type": None
    }

    # Kiểm tra file tồn tại
    while not os.path.exists(inputs["file_path"]):
        console.print("[red]❌ Lỗi: File không tồn tại![/red]")
        inputs["file_path"] = Prompt.ask("🔄 [bold]Nhập lại đường dẫn PDF[/bold]")

    # Kiểm tra số trang
    with pdfplumber.open(inputs["file_path"]) as pdf:
        max_pages = len(pdf.pages)
        while inputs["start_page"] < 1 or inputs["end_page"] > max_pages:
            console.print(f"[red]❌ Phạm vi trang phải từ 1 đến {max_pages}[/red]")
            inputs["start_page"] = IntPrompt.ask("🔄 [bold]Trang bắt đầu mới[/bold]")
            inputs["end_page"] = IntPrompt.ask("🔄 [bold]Trang kết thúc mới[/bold]")
        inputs["start_page"] -=1
        inputs["end_page"] -=1

    # Chọn phong cách
    inputs["style"] = Prompt.ask(
        "🎨 [bold]Phong cách dịch (formal/informal)[/bold]",
        choices=["formal", "informal"],
        default="formal"
    )

    # Chuyên ngành
    inputs["domain"] = Prompt.ask(
        "📚 [bold]Chuyên ngành (Enter để bỏ qua)[/bold]",
        default=None
    )

    # Kiểu dịch
    inputs["translation_type"] = Prompt.ask(
        "📝 [bold]Kiểu dịch[/bold]",
        choices=["literal", "summary", "adaptive"],
        default="adaptive"
    )

    return inputs

def initialize_kernel():
    kernel = Kernel()

    kernel.add_service(OpenAIChatCompletion(
        ai_model_id=CONFIG["model1"]["ai_model_id"],
        async_client=AsyncOpenAI(
            api_key=CONFIG["model1"]["api_key"],
            base_url=CONFIG["model1"]["base_url"]
        )
    ))

    return kernel

kernel = initialize_kernel()

def create_agents(instructions: str):

    translator = ChatCompletionAgent(
        name = "Translator",
        instructions = instructions,
        service = kernel.get_service("openrouter/optimus-alpha")
    )

    return translator

def initialize_db(db_path: str = "translations.db"):

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            chunk_id INTEGER PRIMARY KEY,
            original_text TEXT,
            translated_text TEXT
        )
    """)
    conn.commit()
    return conn

def save_chunk(conn: sqlite3.Connection, chunk_id: int, original: str, translated: str = None):

    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO translations (chunk_id, original_text, translated_text) VALUES (?, ?, ?)",
    (chunk_id, original, translated)
    )
    conn.commit()

def get_translated_chunks(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    cursor = conn.cursor()
    cursor.execute(
    "SELECT chunk_id, translated_text FROM translations WHERE translated_text IS NOT NULL ORDER BY chunk_id"
    )
    return cursor.fetchall()

def extract_text(file_path: str, start_page: int, end_page: int) -> str:
    try:
        with pdfplumber.open(file_path) as pdf:
            logging.info(f"Extracting pages {start_page + 1}-{end_page + 1} from {file_path}")
            pages = pdf.pages[start_page:end_page]
            return "\n".join(page.extract_text() for page in pages if page.extract_text())
    except Exception as e:
        logging.error(f"PDF extraction failed: {str(e)}")
        raise
# import spacy
def split_into_chunks(text: str, max_chunk_size: int = 10000) -> List[str]:
    try:
        chunks = []
        current_chunk: List[str] = []
        current_length = 0

        # nlp = spacy.load("en_core_web_sm")  # Tải mô hình NLP tiếng Anh
        # doc = nlp(text)
        # for sent in doc.sents:
        #     line = sent.text.strip()
        #     if not line:
        #         continue
        lines = text.split('.')
        for line in lines:
            line.strip()
            if not line:
                continue

            line_length = len(line)
            if line_length + current_length > max_chunk_size:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_length = 0

                else:
                    current_chunk.append(line)
                    current_length += line_length
            else:
                current_chunk.append(line)
                current_length += line_length

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        logging.info(f"Tách thành {len(chunks)} chunks")
        return chunks
    except Exception as e:
        logging.error(f"Chunk splitting failed: {str(e)}")
        raise

async def translate(text_chunk: str):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            history = ChatHistory()
            history.add_user_message(text_chunk)

            response = await translator.get_response(messages=history)

            if not response or not str(response).strip():
                    raise ValueError("Empty response from translator")

            return str(response)
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed for chunk ({len(text_chunk)}: {str(e)})\nRetry after 3 seconds")
            await asyncio.sleep(3)

    return text_chunk

async def translate_chunks(chunks: List[str], conn: sqlite3.Connection, max_concurrent: int = 30) -> None:
    semaphore = Semaphore(max_concurrent)
    
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=Console()
    ) as progress:
        main_task = progress.add_task("[cyan]Đang dịch...", total=len(chunks))

        async def translate_with_semaphore(chunk: str, idx: int) -> None:
            async with semaphore:
                translated = await translate(chunk)
                save_chunk(conn, idx, chunk, translated)
                progress.update(main_task, advance=1)  # Cập nhật tiến độ

        tasks = [translate_with_semaphore(chunk, idx) for idx, chunk in enumerate(chunks)]
        await asyncio.gather(*tasks, return_exceptions=True)
        progress.update(main_task, visible=False)

async def main(
    file_path: str, 
    output_md: str,
    start_page: int,
    end_page: int,
    db_path: str = "translations.db") -> bool:
    
    try:
        conn = initialize_db(db_path)
        
        console.print("[bold cyan]📄 Đang trích xuất văn bản từ file PDF...[/bold cyan]")
        raw_text= extract_text(file_path, start_page, end_page)

        console.print("[bold yellow]✂️ Tách văn bản thành các chunks...[/bold yellow]")
        chunks = split_into_chunks(raw_text)

        console.print("[bold green]🌐 Bắt đầu dịch văn bản...[/bold green]")
        await translate_chunks(chunks, conn)

        console.print("[bold magenta]⏳ Đang hoàn tất bản dịch...[/bold magenta]")
        translated_chunks = get_translated_chunks(conn)
        full_md = "\n".join(chunk for _, chunk in translated_chunks)

        with open(output_md, "w", encoding="utf-8") as f:
                f.write(full_md)
        console.print("[bold green]✅ [blink]Hoàn thành bản dịch![/blink][/bold green]", justify="center")
        console.print(f"[bold]🎉 Kết quả đã được lưu vào: [u yellow]{output_md}[/u yellow][/bold]")
        return True

    except Exception as e:
        logging.error(f"Lỗi xảy ra: {str(e)}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

#cơ chế resume
def resume_translation(conn: sqlite3.Connection) -> List[int]:
    """Trả về danh sách các chunk_id đã được dịch"""
    cursor = conn.cursor()
    cursor.execute("SELECT chunk_id FROM translations WHERE translated_text IS NOT NULL")
    return [row[0] for row in cursor.fetchall()]   

if __name__ == "__main__":
    user_config = get_user_input()

    style, domain, translation_type = user_config["style"], user_config["domain"], user_config["translation_type"]

    instructions = build_prompt(style, domain, translation_type)
    translator = create_agents(instructions)

    asyncio.run(main(
        user_config["file_path"],
        output_md="translated.md",
        start_page=user_config["start_page"],
        end_page=user_config["end_page"]
    ))
