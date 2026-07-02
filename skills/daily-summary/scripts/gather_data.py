#!/usr/bin/env python3
# .claude/skills/daily-summary/scripts/gather_data.py

import os, sys, json, subprocess, requests
from datetime import date
from pathlib import Path

def load_config():
    """加载配置文件"""
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def get_git_commits():
    print("## 💻 代码提交")
    try:
        result = subprocess.run(
            ["git", "log", "--since=midnight", "--pretty=format:- %h %s (%an, %ar)", "--no-merges"],
            capture_output=True, text=True
        )
        print(result.stdout.strip() if result.stdout.strip() else "今日暂无代码提交。")
    except Exception as e:
        print(f"获取 Git 记录失败: {e}")
    print("")

def get_claude_logs():
    print("## 🧠 Claude Code 对话记录")
    log_dir = Path.home() / ".claude" / "projects"
    if not log_dir.exists():
        print(f"未找到 Claude Code 日志目录: {log_dir}\n")
        return

    today = date.today().isoformat()
    extracted_logs = []
    for jsonl_file in log_dir.rglob("*.jsonl"):
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        if record.get("timestamp", "").startswith(today) and record.get("type") in ["user", "assistant"]:
                            role = "🙋‍♂️ User" if record["type"] == "user" else "🤖 Claude"
                            content = record.get("message", {}).get("content", "")
                            if isinstance(content, list):
                                content = " ".join([c.get("text", "") for c in content if c.get("type") == "text"])
                            if content: extracted_logs.append(f"{role}: {content[:300]}...")
                    except json.JSONDecodeError: continue
        except Exception as e: print(f"读取文件 {jsonl_file} 失败: {e}")

    print("\n".join(extracted_logs[-20:]) if extracted_logs else "今日暂无 Claude Code 对话记录。")
    print("")

def send_to_server(markdown_content):
    """将日报内容发送到配置的服务器"""
    config = load_config()
    server_cfg = config.get("server", {})
    url = server_cfg.get("url")
    
    if not url:
        return "❌ 发送失败：未在 config.json 中配置 server.url"

    try:
        payload = {
            "date": date.today().isoformat(),
            "content": markdown_content
        }
        response = requests.request(
            method=server_cfg.get("method", "POST"),
            url=url,
            headers=server_cfg.get("headers", {}),
            json=payload,
            timeout=10
        )
        if response.ok:
            return f"✅ 发送成功！服务器响应状态码: {response.status_code}"
        else:
            return f"❌ 发送失败：状态码 {response.status_code}，响应: {response.text[:200]}"
    except Exception as e:
        return f"❌ 发送异常: {str(e)}"

def main():
    print(f"=== 📅 今日工作数据 ===\n日期: {date.today().isoformat()}\n")
    get_git_commits()
    get_claude_logs()

if __name__ == "__main__":
    # 支持通过命令行参数触发发送动作
    if len(sys.argv) > 1 and sys.argv[1] == "--send":
        # 从标准输入读取日报内容
        content = sys.stdin.read()
        print(send_to_server(content))
    else:
        main()