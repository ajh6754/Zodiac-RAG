from openai import OpenAI, RateLimitError
from dotenv import load_dotenv, find_dotenv
import os
import re
import json
import time
import shutil
import argparse
import datetime
import subprocess
from rules import RuleStore

# Example: python generate.py --models qwen3.8:27b claude-opus-5-5 --reps 3 --run examplerun

HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM_PROMPT = (
  "You are a Terraform code generator for Microsoft Azure using the azurerm provider (version 4.x). "
  "Return one complete, deployable Terraform configuration in a single ```hcl code block and nothing else."
)

RULES_INTRO = (
  "The following rules were mined from real Azure Terraform projects. "
  "Configurations that break them often fail at deployment. Follow them where they apply:\n"
)

def build_prompt(task: dict, condition: str, store: RuleStore, top_k: int) -> tuple:
  """Returns the user prompt for a task, with retrieved rules added for the zodiac condition."""
  if condition == "baseline":
    return task["prompt"], []
  rules = store.retrieve(task["prompt"], task["resources"], top_k)
  rule_list = "\n".join(f"- {rule}" for rule in rules)
  return RULES_INTRO + rule_list + "\n\nTask: " + task["prompt"], rules

def extract_code(text: str) -> str:
  """Pulls the Terraform out of the model's reply."""
  blocks = re.findall(r"```(?:hcl|terraform|tf)?\s*\n(.*?)```", text, re.S)
  return max(blocks, key=len) if blocks else text

def ask_gccis(client: OpenAI, model: str, prompt: str, args) -> dict:
  """Asks a GCCIS model. GCCIS allows 10k tokens per minute, so on a 429 we wait and retry."""
  extra = {} if args.reasoning == "default" else {"reasoning_effort": args.reasoning}
  options = {"max_tokens": args.max_tokens}
  if args.temperature is not None:
    options["temperature"] = args.temperature

  while True:
    try:
      response = client.chat.completions.create(
        model=model,
        messages=[
          {"role": "system", "content": SYSTEM_PROMPT},
          {"role": "user", "content": prompt}
        ],
        extra_body=extra,
        **options
      )
      break
    except RateLimitError:
      print("  rate limited, waiting 60s")
      time.sleep(60)

  return {
    "text": response.choices[0].message.content or "",
    "prompt_tokens": response.usage.prompt_tokens,
    "output_tokens": response.usage.completion_tokens,
    "finish_reason": response.choices[0].finish_reason
  }

def ask_claude(model: str, prompt: str, args) -> dict:
  """Asks Claude through the Claude Code CLI"""
  # Claude Code adds a bit of its own text to every call, so turn off everything we can
  # to keep that as small as possible every time
  env = {k: v for k, v in os.environ.items()
         if not k.startswith(("CLAUDE", "ANTHROPIC")) and k not in ("PWD", "OLDPWD")}
  command = [shutil.which("claude"), "-p", "--model", model, "--system-prompt", SYSTEM_PROMPT,
             "--tools", "", "--strict-mcp-config", "--setting-sources", "",
             "--no-session-persistence", "--output-format", "json"]
  if args.reasoning != "default":
    command += ["--effort", args.reasoning]

  drive_root = os.path.splitdrive(HERE)[0] + os.sep
  result = subprocess.run(command, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", cwd=drive_root, env=env)
  data = json.loads(result.stdout)
  if data.get("is_error"):
    raise RuntimeError(data.get("result"))

  # Sometimes Claude redirects to a model, if the requested one isn't available (don't allow)
  used = list(data.get("modelUsage", {}).keys())
  if used != [model]:
    raise RuntimeError(f"asked for {model} but got {used}")

  usage = data["usage"]
  return {
    "text": data["result"],
    "prompt_tokens": usage["input_tokens"] + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
    "output_tokens": usage["output_tokens"],
    "finish_reason": data.get("stop_reason")
  }

def ask_codex(model: str, prompt: str, args) -> dict:
  """Asks Codex through the Codex CLI."""
  model = model.removeprefix("codex:")
  codex_path = shutil.which("codex")
  if not codex_path:
    raise RuntimeError("Codex CLI not found on PATH")
  command = [codex_path, "exec", "--model", model, "--json", "--ephemeral",
             "--skip-git-repo-check", "--ignore-user-config", "--sandbox", "read-only",
             "-c", "developer_instructions=" + json.dumps(SYSTEM_PROMPT),
             "-c", "features.shell_tool=false", "-"]
  if args.reasoning != "default":
    command += ["-c", "model_reasoning_effort=" + json.dumps(args.reasoning)]

  env = {k: v for k, v in os.environ.items()
         if not k.startswith("CODEX_") or k == "CODEX_HOME"}
  drive_root = os.path.splitdrive(HERE)[0] + os.sep
  result = subprocess.run(command, input=SYSTEM_PROMPT + "\n\n" + prompt, capture_output=True, text=True,
                          encoding="utf-8", cwd=drive_root, env=env)
  if result.returncode:
    raise RuntimeError(result.stderr or result.stdout)

  text = ""
  usage = None
  for line in result.stdout.splitlines():
    event = json.loads(line)
    # "error" events also include retries ("Reconnecting... 2/5"), we should only exit on a true failed turn
    if event["type"] == "turn.failed":
      raise RuntimeError(event.get("error", event.get("message")))
    if event["type"] == "item.completed":
      item = event["item"]
      if item["type"] == "agent_message":
        text = item["text"]
    elif event["type"] == "turn.completed":
      usage = event["usage"]
  if usage is None or not text:
    raise RuntimeError("Codex returned no completed response")

  return {
    "text": text,
    "prompt_tokens": usage["input_tokens"],
    "output_tokens": usage["output_tokens"],
    "finish_reason": "completed"
  }

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--models", nargs="+", default=["qwen3.8:27b"])
  parser.add_argument("--conditions", nargs="+", default=["baseline", "zodiac"])
  parser.add_argument("--tasks", nargs="*", help="task ids from tasks.json (default: all)")
  parser.add_argument("--reps", type=int, default=1)
  parser.add_argument("--top-k", type=int, default=15)
  parser.add_argument("--kb", default="mined", choices=["mined", "validated"])
  parser.add_argument("--reasoning", default="default", help="default, none, low, medium, high")
  parser.add_argument("--temperature", type=float, default=None)  # temperature 0 made Qwen repeat itself forever
  parser.add_argument("--max-tokens", type=int, default=8000)
  parser.add_argument("--run", default=datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
  args = parser.parse_args()

  with open(os.path.join(HERE, "tasks.json"), encoding="utf-8") as f:
    tasks = json.load(f)
  if args.tasks:
    tasks = [t for t in tasks if t["id"] in args.tasks]

  load_dotenv(find_dotenv())
  rit_api_key = os.getenv("RIT_API_KEY") or os.getenv("GCCIS_API_KEY")
  client = OpenAI(
    base_url="https://api.genai.gccis.rit.edu/v1",
    api_key=rit_api_key,
    timeout=900
  ) if any(not m.startswith("claude-") and "codex" not in m for m in args.models) else None
  store = RuleStore(args.kb) if "zodiac" in args.conditions else None

  run_dir = os.path.join(HERE, "results", args.run)
  os.makedirs(run_dir, exist_ok=True)
  with open(os.path.join(run_dir, "config.json"), "w") as f:
    json.dump(vars(args), f, indent=2)

  for model in args.models:
    for task in tasks:
      for condition in args.conditions:
        for rep in range(1, args.reps + 1):
          out_dir = os.path.join(run_dir, model.replace(":", "_"), task["id"], condition, f"rep{rep}")
          if os.path.exists(os.path.join(out_dir, "main.tf")):
            continue  # already done, so a stopped run can be restarted if needed with the same --run

          print(f"{model} | {task['id']} | {condition} | rep {rep}")
          prompt, rules = build_prompt(task, condition, store, args.top_k)
          start = time.time()
          if model.startswith("claude-"):
            answer = ask_claude(model, prompt, args)
          elif "codex" in model:
            answer = ask_codex(model, prompt, args)
          else:
            answer = ask_gccis(client, model, prompt, args)
          seconds = round(time.time() - start, 1)
          print(f"  {seconds}s, {answer['output_tokens']} output tokens")

          os.makedirs(out_dir, exist_ok=True)
          with open(os.path.join(out_dir, "main.tf"), "w", encoding="utf-8") as f:
            f.write(extract_code(answer["text"]))
          with open(os.path.join(out_dir, "response.md"), "w", encoding="utf-8") as f:
            f.write(answer["text"])
          with open(os.path.join(run_dir, "log.jsonl"), "a", encoding="utf-8") as f:
            log = {
              "model": model, "task": task["id"], "condition": condition, "rep": rep,
              "seconds": seconds, "finish_reason": answer["finish_reason"],
              "prompt_tokens": answer["prompt_tokens"], "output_tokens": answer["output_tokens"],
              "rules": rules
            }
            f.write(json.dumps(log) + "\n")

if __name__ == "__main__":
  main()
