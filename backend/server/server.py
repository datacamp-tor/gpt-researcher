import json
import os
from typing import Dict, List
import time
import asyncio
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, File, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.server.websocket_manager import WebSocketManager
from backend.server.server_utils import (
    get_config_dict, sanitize_filename,
    update_environment_variables, handle_file_upload, handle_file_deletion,
    execute_multi_agents, handle_websocket_communication
)

from backend.server.websocket_manager import run_agent
from backend.utils import write_md_to_word, write_md_to_pdf
from gpt_researcher.utils.logging_config import setup_research_logging
from gpt_researcher.utils.enum import Tone
from backend.chat.chat import ChatAgentWithMemory
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import logging

# Get logger instance
logger = logging.getLogger(__name__)

# Don't override parent logger settings
logger.propagate = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()  # Only log to console
    ]
)

# Models


class ResearchRequest(BaseModel):
    task: str
    report_type: str
    report_source: str
    tone: str
    headers: dict | None = None
    repo_name: str
    branch_name: str
    generate_in_background: bool = True
    language: Optional[str] = None 


class ConfigRequest(BaseModel):
    ANTHROPIC_API_KEY: str
    TAVILY_API_KEY: str
    LANGCHAIN_TRACING_V2: str
    LANGCHAIN_API_KEY: str
    OPENAI_API_KEY: str
    DOC_PATH: str
    RETRIEVER: str
    GOOGLE_API_KEY: str = ''
    GOOGLE_CX_KEY: str = ''
    BING_API_KEY: str = ''
    SEARCHAPI_API_KEY: str = ''
    SERPAPI_API_KEY: str = ''
    SERPER_API_KEY: str = ''
    SEARX_URL: str = ''
    XAI_API_KEY: str
    DEEPSEEK_API_KEY: str

class SummaryRequest(BaseModel):
    name: str
    author: str
    publication_date: str


# App initialization
app = FastAPI()

# Static files and templates
app.mount("/site", StaticFiles(directory="./frontend"), name="site")
app.mount("/static", StaticFiles(directory="./frontend/static"), name="static")
templates = Jinja2Templates(directory="./frontend")

# WebSocket manager
manager = WebSocketManager()

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
DOC_PATH = os.getenv("DOC_PATH", "./my-docs")
base_url = os.getenv("BASE_URL")

# Startup event


@app.on_event("startup")
def startup_event():
    os.makedirs("outputs", exist_ok=True)
    app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
    # os.makedirs(DOC_PATH, exist_ok=True)  # Commented out to avoid creating the folder if not needed
    

# Routes


@app.get("/")
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "report": None})


@app.get("/report/{research_id}")
async def read_report(request: Request, research_id: str):
    docx_path = os.path.join('outputs', f"{research_id}.docx")
    if not os.path.exists(docx_path):
        return {"message": "Report not found."}
    return FileResponse(docx_path)


async def write_report(research_request: ResearchRequest, research_id: str = None):
    from gpt_researcher.config.config import Config

    cfg = Config()

    report_information = await run_agent(
        task=research_request.task,
        report_type=research_request.report_type,
        report_source=research_request.report_source,
        source_urls=[],
        document_urls=[],
        tone=Tone[research_request.tone],
        websocket=None,
        stream_output=None,
        headers=research_request.headers,
        query_domains=[],
        config_path="",
        return_researcher=True,
        language=cfg.language
    )

    docx_path = await write_md_to_word(report_information[0], research_id)
    pdf_path = await write_md_to_pdf(report_information[0], research_id)
    if research_request.report_type != "multi_agents":
        report, researcher = report_information
        response = {
            "research_id": research_id,
            "research_information": {
                "source_urls": researcher.get_source_urls(),
                "research_costs": researcher.get_costs(),
                "visited_urls": list(researcher.visited_urls),
                "research_images": researcher.get_research_images(),
                # "research_sources": researcher.get_research_sources(),  # Raw content of sources may be very large
            },
            "report": report,
            "docx_path": docx_path,
            "pdf_path": pdf_path
        }
    else:
        response = { "research_id": research_id, "report": "", "docx_path": docx_path, "pdf_path": pdf_path }

    return response

@app.post("/report/")
async def generate_report(research_request: ResearchRequest, background_tasks: BackgroundTasks):
    research_id = sanitize_filename(f"task_{int(time.time())}_{research_request.task}")

    if research_request.generate_in_background:
        background_tasks.add_task(write_report, research_request=research_request, research_id=research_id)
        return {"message": "Your report is being generated in the background. Please check back later.",
                "research_id": research_id}
    else:
        response = await write_report(research_request, research_id)
        return response


@app.get("/files/")
async def list_files():
    if not os.path.exists(DOC_PATH):
        os.makedirs(DOC_PATH, exist_ok=True)
    files = os.listdir(DOC_PATH)
    print(f"Files in {DOC_PATH}: {files}")
    return {"files": files}


@app.post("/api/multi_agents")
async def run_multi_agents():
    return await execute_multi_agents(manager)


@app.post("/upload/")
async def upload_file(file: UploadFile = File(...)):
    return await handle_file_upload(file, DOC_PATH)


@app.delete("/files/{filename}")
async def delete_file(filename: str):
    return await handle_file_deletion(filename, DOC_PATH)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await handle_websocket_communication(websocket, manager)
    except WebSocketDisconnect:
        await manager.disconnect(websocket)

@app.post("/generate-summary")
async def generate_summary(request: SummaryRequest):
    try:
        chinese_task = (
            f"请用中文帮我开展一次深度研究，帮助我快速、全面、深入地理解以下这本书：\n\n"
            f"书名：{request.name}\n"
            f"作者：{request.author}\n"
            f"出版时间：{request.publication_date}\n\n"
            f"请**仅使用英文资料**进行搜索和信息采集，但全部用**中文撰写报告**。\n\n"
            f"请围绕以下维度进行详细分析（不限于此）：\n"
            f"- 引言\n"
            f"- 内容结构\n"
            f"- 核心观点\n"
            f"- 目标读者/适用人群\n"
            f"- 现实意义或社会影响\n"
            f"- 与其他类似书籍的比较与差异\n"
            f"- **逐章深度解读（每一章分别详细介绍其核心内容、关键情节和观点，如有章节标题也请注明）**\n"
            f"- 结论\n"
            f"报告应尽量详尽，篇幅尽可能长一些，务求深刻与全面，不需总结简略，请详细解读。\n\n"
            f"请注意：**正文中不要插入任何超链接或网页链接**，如需引用资料，请统一列在报告末尾的“参考资料”部分。"
        )

        english_task = (
            f"Please conduct an in-depth research to help me fully understand the following book:\n\n"
            f"Title: {request.name}\n"
            f"Author: {request.author}\n"
            f"Publication Date: {request.publication_date}\n\n"
            f"Use only English sources for research, and write the entire report in **English**.\n\n"
            f"Please cover the following aspects in detail (but not limited to these):\n"
            f"- Introduction\n"
            f"- Structure of the content\n"
            f"- Core ideas and arguments\n"
            f"- Target audience\n"
            f"- Real-world significance or societal impact\n"
            f"- Comparisons with similar books\n"
            f"- **Chapter-by-chapter deep dive (introduce the main content, key plots, and ideas of each chapter, mentioning chapter titles if available)**\n"
            f"- Conclusion\n"
            f"The report should be as detailed as possible, with no word limit. Avoid brief summaries — aim for depth and comprehensiveness.\n\n"
            f"**Do not insert any hyperlinks or URLs in the body**. If citing sources, list them at the end under 'References'."
        )

        chinese_request = ResearchRequest(
            task=chinese_task,
            report_type="basic_report",
            report_source="web",
            tone="Objective",
            headers=None,
            repo_name="book-summary-api",
            branch_name="main",
            generate_in_background=False,
            language="chinese"
        )
        english_request = ResearchRequest(
            task=english_task,
            report_type="basic_report",
            report_source="web",
            tone="Objective",
            headers=None,
            repo_name="book-summary-api",
            branch_name="main",
            generate_in_background=False,
            language="english"
        )

        chinese_id = sanitize_filename(f"summary_cn_{int(time.time())}_{request.name[:20]}")
        english_id = sanitize_filename(f"summary_en_{int(time.time())}_{request.name[:20]}")

        chinese_result, english_result = await asyncio.gather(
            write_report(chinese_request, research_id=chinese_id),
            write_report(english_request, research_id=english_id)
        )

        return {
            "summary_chinese": chinese_result.get("report", "No Chinese report returned"),
            "research_id_chinese": chinese_result.get("research_id"),
            "pdf_url_chinese": f"{base_url}/outputs/{chinese_result.get('research_id')}.pdf",
            "summary_english": english_result.get("report", "No English report returned"),
            "research_id_english": english_result.get("research_id"),
            "pdf_url_english": f"{base_url}/outputs/{english_result.get('research_id')}.pdf"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
