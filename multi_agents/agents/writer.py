from datetime import datetime
import json5 as json
from .utils.views import print_agent_output
from .utils.llms import call_model

sample_json = """
{
  "目录": 使用 markdown 的格式（使用 '-'）编写的目录，基于研究的标题和小节标题,
  "引言": 根据研究数据写出的详细引言，使用 markdown 语法，包含相关来源的超链接引用,
  "结论": 基于所有研究数据写出的总结段落，使用 markdown 语法，包含相关来源的超链接引用,
  "参考文献": 使用 markdown 语法和 APA 引用格式列出所有使用过的来源链接，例如：['- 标题，年份，作者 [来源链接](链接)', ...]
}
"""


class WriterAgent:
    def __init__(self, websocket=None, stream_output=None, headers=None):
        self.websocket = websocket
        self.stream_output = stream_output
        self.headers = headers

    def get_headers(self, research_state: dict):
        return {
            "title": research_state.get("title"),
            "date": "日期",
            "introduction": "引言",
            "table_of_contents": "目录",
            "conclusion": "结论",
            "references": "参考资料",
        }

    async def write_sections(self, research_state: dict):
        query = research_state.get("title")
        data = research_state.get("research_data")
        task = research_state.get("task")
        follow_guidelines = task.get("follow_guidelines")
        guidelines = task.get("guidelines")

        prompt = [
            {
                "role": "system",
                "content": "你是一位研究写作者，你的任务是根据给定的研究数据，撰写高质量的研究报告内容，包括详细的引言和结论部分。请使用中文撰写。",
            },
            {
                "role": "user",
                "content": f"""今天的日期是：{datetime.now().strftime('%Y年%m月%d日')}
研究主题：{query}
研究数据如下：{str(data)}

请你根据以上研究数据完成以下任务：
1. 用中文撰写一段详细的引言（不需要加标题）。
2. 用中文撰写一段全面的结论（不需要加标题）。
3. 使用 markdown 的格式为报告创建一个目录（以 "-" 开头的列表），基于研究标题和小节标题生成。
4. 以 markdown 和 APA 引用格式列出所有引用来源，示例：'- 标题，年份，作者 [来源链接](链接)'。

{f'请务必遵循以下写作指南：{guidelines}' if follow_guidelines else ''}

请仅返回一个 JSON 字符串，结构如下（不要包含 markdown 代码块标记）：
{sample_json}
""",
            },
        ]

        response = await call_model(
            prompt,
            task.get("model"),
            response_format="json",
        )
        return response

    async def revise_headers(self, task: dict, headers: dict):
        prompt = [
            {
                "role": "system",
                "content": "你是一位研究写作者。你的任务是根据写作指南，将以下报告标题信息翻译和调整为简洁、准确的中文版本。",
            },
            {
                "role": "user",
                "content": f"""请根据以下写作指南对报告标题进行修订，并全部翻译为中文。注意：
- 所有值必须为中文；
- 不要使用任何 markdown 语法；
- 返回内容必须为 JSON 格式，结构应与 headers 数据保持一致；
- 不要添加额外内容。

写作指南：{task.get("guidelines")}

原始标题数据如下：
{headers}
""",
            },
        ]

        response = await call_model(
            prompt,
            task.get("model"),
            response_format="json",
        )
        return {"headers": response}

    async def run(self, research_state: dict):
        if self.websocket and self.stream_output:
            await self.stream_output(
                "logs",
                "writing_report",
                f"Writing final research report based on research data...",
                self.websocket,
            )
        else:
            print_agent_output(
                f"Writing final research report based on research data...",
                agent="WRITER",
            )

        research_layout_content = await self.write_sections(research_state)

        if research_state.get("task").get("verbose"):
            if self.websocket and self.stream_output:
                research_layout_content_str = json.dumps(
                    research_layout_content, indent=2
                )
                await self.stream_output(
                    "logs",
                    "research_layout_content",
                    research_layout_content_str,
                    self.websocket,
                )
            else:
                print_agent_output(research_layout_content, agent="WRITER")

        headers = self.get_headers(research_state)
        if research_state.get("task").get("follow_guidelines"):
            if self.websocket and self.stream_output:
                await self.stream_output(
                    "logs",
                    "rewriting_layout",
                    "Rewriting layout based on guidelines...",
                    self.websocket,
                )
            else:
                print_agent_output(
                    "Rewriting layout based on guidelines...", agent="WRITER"
                )
            headers = await self.revise_headers(
                task=research_state.get("task"), headers=headers
            )
            headers = headers.get("headers")

        return {**research_layout_content, "headers": headers}
