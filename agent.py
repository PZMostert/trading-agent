import subprocess
import requests
import json
import re

MCP_CLI = r"E:\trading-agent\Trading\tradingview-mcp\src\cli\index.js"
NODE    = r"C:\Program Files\nodejs\node.exe"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen2.5-coder:3b"

def generate_pine(prompt):
    print("\nThinking...")
    payload = {
        "model": MODEL,
        "prompt": f"Write a Pine Script v5 strategy for TradingView. Output ONLY the raw Pine Script code with no markdown, no backticks, no explanation, no ```pinescript tags. Start directly with //@version=5\n\n{prompt}",
        "stream": False
    }
    response = requests.post(OLLAMA_URL, json=payload)
    result = response.json()
    script = result["response"]
    script = re.sub(r'`+\w*', '', script)
    script = script.strip()
    lines = [l for l in script.split('\n') if l.strip()]
    if lines and not lines[0].strip().startswith('//'):
        lines = lines[1:]
    script = '\n'.join(lines).strip()
    return script

def inject_pine(script):
    with open(r"E:\trading-agent\Trading\agent\temp_strategy.pine", "w") as f:
        f.write(script)
    result = subprocess.run(
        [NODE, MCP_CLI, "pine", "set", "--file", r"E:\trading-agent\agent\temp_strategy.pine"],
        capture_output=True, text=True
    )
    print(result.stdout)
    result = subprocess.run(
        [NODE, MCP_CLI, "pine", "compile"],
        capture_output=True, text=True
    )
    print(result.stdout)

def main():
    print("=== Pine Script Agent ===")
    print("Type your strategy in plain English and press Enter.")
    print("Type 'quit' to exit.\n")
    while True:
        prompt = input("> ")
        if prompt.lower() == "quit":
            break
        script = generate_pine(prompt)
        print("\n--- Generated Pine Script ---")
        print(script)
        print("-----------------------------")
        choice = input("\nInject into TradingView? (y/n): ")
        if choice.lower() == "y":
            inject_pine(script)
            print("Done!")

if __name__ == "__main__":
    main()