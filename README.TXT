Zodiac-RAG tests whether adding Zodiac's mined Terraform rules to an LLM prompt
changes the security of the Azure Terraform the LLM writes.

Our code is in poc/. Everything else is just from the original Zodiac repo, plus the
rules we mined using it (ruleJsonFiles/) as well as our Phase 1 proposal (docs/).

Layout (poc/)
  rules.py - loads the mined rules and retrieves the relevant ones for the task
  tasks.json - the 19 Azure tasks, as well as the resource types each one uses
  generate.py - asks a model to generate Terraform code, with and without the rules
  scan.py - then checks each output with terraform plan and Checkov
  check_labels.csv - which Checkov checks count as "core" security failures (subjective)
  results/ - our final runs (checkov.csv, log.jsonl, config.json per run)
  main.py - basic original RAG proof of concept

Install
  1. Python 3.11+, then run `pip install -r poc/requirements.txt`
  2. Terraform and the Azure CLI (for terraform plan)
  3. Optional, if using those models: Claude Code CLI (claude) and Codex CLI (codex), logged in to a subscription
  4. Create poc/.env with:
      RIT_API_KEY=<GCCIS GenAI key> - for GCCIS models
      AZURE_CONFIG_DIR=<folder with an az login> - for terraform plan
      ARM_SUBSCRIPTION_ID=<Azure subscription id> - for terraform plan

Usage (run from poc/)
  python generate.py --models <model> --reps 3 --reasoning high --run <name>
  python scan.py results/<name>

  GCCIS models are written like "qwen3.8:27b", and Claude and Codex are "claude-<model>" (claude-opus-5-5),
  or "codex:<model>" (codex:gpt-6.1-sol).
  Other options: --tasks <ids>, --conditions baseline zodiac, --top-k 15. Runs can also be restarted/resumed with the same --run name and skip what's done.

  Output: results/<name>/<model>/<task>/<baseline|zodiac>/rep<N>/main.tf, plus
  log.jsonl (prompts, rules used, tokens) and checkov.csv (plan result and
  Checkov failures per file, with a summary printed at the end).

Demo
  python generate.py --models claude-opus-5-5 --tasks storage_account --reasoning high --run demo
  python scan.py results/demo
