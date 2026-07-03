#!/usr/bin/env python3
# .claude/skills/daily-summary/scripts/gather_data.py

import os, sys, json, copy, subprocess, requests, time, re
from datetime import date, datetime
from pathlib import Path

# 1. 自动加载项目根目录的 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env") 
except ImportError:
    pass 

def load_config():
    """加载配置文件"""
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def resolve_env_vars(obj):
    """递归解析配置中的环境变量占位符"""
    if isinstance(obj, dict): return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list): return [resolve_env_vars(item) for item in obj]
    elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        return os.environ.get(obj[2:-1], "")
    return obj

# ================= 数据采集模块 =================
def get_git_commits():
    print("##  代码提交")
    if not (Path.cwd() / ".git").exists():
        print("️ 当前目录非 Git 仓库，跳过代码采集。\n")
        return
    try:
        result = subprocess.run(
            ["git", "log", "--since=midnight", "--pretty=format:- %h %s (%an, %ar)", "--no-merges"],
            capture_output=True, text=True
        )
        print(result.stdout.strip() if result.stdout.strip() else "今日暂无代码提交。\n")
    except Exception as e: 
        print(f"获取 Git 记录失败: {e}\n")

def get_claude_logs():
    print("##  Claude Code 对话记录")
    log_dir = Path.home() / ".claude" / "projects"
    if not log_dir.exists():
        print(f"未找到 Claude Code 日志目录: {log_dir}\n")
        return
    
    today = date.today()
    extracted_logs = []
    
    for jsonl_file in log_dir.rglob("*.jsonl"):
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        ts_str = record.get("timestamp", "")
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts.date() != today: continue
                        except ValueError:
                            continue
                            
                        if record.get("type") in ["user", "assistant"]:
                            role = "‍♂️ User" if record["type"] == "user" else " Claude"
                            content = record.get("message", {}).get("content", "")
                            if isinstance(content, list):
                                content = " ".join([c.get("text", "") for c in content if c.get("type") == "text"])
                            
                            if content and "password" not in content.lower():
                                extracted_logs.append(f"{role}: {content[:300]}...")
                    except json.JSONDecodeError: 
                        continue
        except Exception as e: 
            print(f"读取文件 {jsonl_file} 失败: {e}")
            
    print("\n".join(extracted_logs[-20:]) if extracted_logs else "今日暂无 Claude Code 对话记录。")
    print("")

# ================= 解析与模板替换模块 =================
def parse_tasks_from_markdown(markdown_content):
    """增强版正则：兼容中英文冒号、阿拉伯数字与中文数字"""
    pattern = r'(?=### 任务\s*[\d一二三四五六七八九十]+\s*[:：])'
    chunks = re.split(pattern, markdown_content)
    
    tasks = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk: continue
        
        first_line = chunk.split('\n')[0]
        hours_match = re.search(r'预估总耗时\s*[:：]\s*([\d.]+)', first_line)
        ai_hours_match = re.search(r'AI 辅助耗时\s*[:：]\s*([\d.]+)', chunk)
        token_match = re.search(r'Token 消耗量\s*[:：]\s*(\d+)', chunk)
        
        tasks.append({
            "hours": float(hours_match.group(1)) if hours_match else 0.0,
            "ai_hours": float(ai_hours_match.group(1)) if ai_hours_match else 0.0,
            "token_consume": int(token_match.group(1)) if token_match else 0,
            "content": chunk
        })
    return tasks

def replace_placeholders(obj, replacements):
    """递归替换字典中的占位符 {{key}}"""
    if isinstance(obj, dict): return {k: replace_placeholders(v, replacements) for k, v in obj.items()}
    elif isinstance(obj, list): return [replace_placeholders(item, replacements) for item in obj]
    elif isinstance(obj, str):
        for key, value in replacements.items(): 
            obj = obj.replace(f"{{{{{key}}}}}", str(value))
        return obj
    return obj

# ================= 发送模块 =================
def send_to_server(markdown_content):
    """将结构化任务列表打包到 Payload 中，一次性发送到服务器"""
    config = resolve_env_vars(load_config())
    server_cfg = config.get("server", {})
    url = server_cfg.get("url")
    
    if not url: 
        return " 发送失败：未在 config.json 中配置 server.url"

    # 1. 解析任务列表
    tasks = parse_tasks_from_markdown(markdown_content) 
    if not tasks:
        return " 发送失败：未能从日报中解析出任何任务块，请检查 Markdown 格式是否符合规范。"

    # 2. 组装结构化 List<Object>
    task_item_template = config.get("task_item_template", {"content": "{{content}}"})
    structured_tasks = [replace_placeholders(copy.deepcopy(task_item_template), task) for task in tasks]

    # 3. 组装主 Payload
    main_template = config.get("payload_template", {"tasks": "{{tasks}}"})
    final_payload = replace_placeholders(copy.deepcopy(main_template), {
        "tasks": structured_tasks,
        "date": date.today().isoformat()
    })

    # 4. 发送前确认（预览）
    print(f" 共解析到 {len(tasks)} 个独立任务，已打包至 Payload。")
    print("️ 即将发送以下 Payload 到服务器:")
    print(json.dumps(final_payload, indent=2, ensure_ascii=False))
    
    # 如果是管道传入（非交互模式），默认直接发送，避免阻塞
    if not sys.stdin.isatty():
        confirm = 'y'
    else:
        confirm = input("\n确认发送吗？(y/n): ").strip().lower()
        
    if confirm != 'y': 
        return " 发送已取消。"

    # 5. 执行发送（带自动重试）
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f" 正在发送 (第 {attempt} 次尝试)...")
            response = requests.request(
                method=server_cfg.get("method", "POST"), url=url,
                headers=server_cfg.get("headers", {}), json=final_payload, timeout=10
            )
            if response.ok:
                return f" 发送成功！状态码: {response.status_code}"
            else:
                print(f"️ 服务器返回异常: {response.status_code}")
        except Exception as e:
            print(f"️ 网络异常: {str(e)}")
        
        if attempt < max_retries:
            print(" 3秒后自动重试...")
            time.sleep(3)
            
    return " 发送失败：已达到最大重试次数，请检查网络或服务器配置。"

# ================= 主入口 =================
def main():
    print(f"===  今日工作数据 ===\n日期: {date.today().isoformat()}\n")
    get_git_commits()
    get_claude_logs()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--send":
        # 支持管道读取内容，完美适配解耦后的发送逻辑
        content = sys.stdin.read()
        if not content.strip():
            print(" 错误：未通过管道传入日报内容。")
        else:
            print(send_to_server(content))
    else:
        main()