import json
import re
import ssl
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_URL

# ============================================================
# 配置
# ============================================================
PORT = 8765

# ============================================================
# 题型映射
# ============================================================
TYPE_NAMES = {"0": "单选题", "1": "多选题", "2": "填空题", "3": "判断题", "4": "简答题", "6": "简答题"}


def build_prompt(question_text, options, qtype):
    """根据题型构造不同的 prompt"""
    type_name = TYPE_NAMES.get(qtype, qtype)
    options_text = "\n".join(f"{chr(65 + i)}. {opt}" for i, opt in enumerate(options)) if options else ""

    if qtype in ("0", "1"):  # 单选 / 多选
        multi = "至少两个正确选项" if qtype == "1" else "一个正确选项"
        return f"""你是一个学习助手。请回答以下{type_name}，只返回 JSON，不要任何解释。

题目：{question_text}
选项：
{options_text}

要求：{multi}。返回格式如下：
单选：{{"answer": ["正确选项的完整文本"]}}
多选：{{"answer": ["选项A文本", "选项B文本"]}}

只返回 JSON："""

    elif qtype == "2":  # 填空
        return f"""你是一个学习助手。请回答以下{type_name}，只返回 JSON，不要任何解释。

题目：{question_text}

返回格式：{{"answer": ["填空1答案", "填空2答案"]}}
如果只有一个空：{{"answer": ["答案"]}}

只返回 JSON："""

    elif qtype == "3":  # 判断
        return f"""你是一个学习助手。请回答以下{type_name}，只返回 JSON，不要任何解释。

题目：{question_text}

返回格式：正确则 {{"answer": ["对"]}}，错误则 {{"answer": ["错"]}}

只返回 JSON："""

    else:  # 简答等
        return f"""你是一个学习助手。请回答以下{type_name}，只返回 JSON，不要任何解释。

题目：{question_text}
{("选项：" + chr(10) + options_text) if options else ""}

返回格式：{{"answer": ["你的答案"]}}

只返回 JSON："""


def call_deepseek(prompt):
    """调用 DeepSeek API"""
    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个精准的答题助手。你必须只返回合法的 JSON，不要包含 markdown 代码块标记，不要任何额外文字。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 512
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(DEEPSEEK_URL, data=body)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {DEEPSEEK_API_KEY}")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json(text):
    """从 DeepSeek 返回内容中提取 JSON，处理可能包裹在 ```json ``` 中的情况"""
    text = text.strip()
    # 去掉 markdown 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 找到第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and start < end:
        return json.loads(text[start:end + 1])
    raise ValueError(f"无法从响应中提取 JSON: {text[:200]}")


def process_question(question_text, options, qtype, question_data):
    """处理一道题，返回脚本期望的格式"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    type_name = TYPE_NAMES.get(qtype, qtype)

    print(f"\n{'─'*50}")
    print(f"[{ts}] {type_name}: {question_text[:80]}{'...' if len(question_text) > 80 else ''}")
    if options:
        for i, opt in enumerate(options):
            print(f"  {chr(65+i)}. {opt}")

    try:
        prompt = build_prompt(question_text, options, qtype)
        resp = call_deepseek(prompt)

        content = resp["choices"][0]["message"]["content"]
        print(f"DeepSeek 原始回复: {content[:200]}")

        data = extract_json(content)
        answer = data.get("answer", [])
        print(f"解析后答案: {answer}")

        return {
            "code": 200,
            "data": {
                "answer": answer,
                "num": "0"
            }
        }
    except Exception as e:
        print(f"DeepSeek 调用失败: {e}")
        return {
            "code": 10003,
            "msg": f"DeepSeek 调用失败: {str(e)[:100]}"
        }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length else b""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError:
            self._reply(200, {"code": 10003, "msg": "请求体 JSON 解析失败"})
            return

        # 打印请求摘要
        print(f"\n{'='*60}")
        print(f"[{ts}] POST {self.path}")
        print(f"  课程ID: {body.get('id', '?')}  |  题型: {TYPE_NAMES.get(body.get('type', ''), '?')}  |  类型: {body.get('workType', '?')}")
        print(f"  题目: {body.get('question', '')[:100]}")

        # 调用 DeepSeek 处理
        result = process_question(
            body.get("question", ""),
            body.get("options", []),
            body.get("type", ""),
            body.get("questionData", "")
        )

        self._reply(200, result)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _reply(self, code, data):
        try:
            resp_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
            self.wfile.write(resp_bytes)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    if not DEEPSEEK_API_KEY:
        print("⚠️  请先在 config.py 中设置 DEEPSEEK_API_KEY！\n")
    print(f"题库服务启动: http://127.0.0.1:{PORT}/search")
    print("Ctrl+C 停止\n")
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
