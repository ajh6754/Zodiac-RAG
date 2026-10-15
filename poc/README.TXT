Generates Azure Terraform files with GCCIS models, with and without Zodiac
rules, then scans output with Checkov and terraform plan.

Setup: pip install -r requirements.txt, and setup .env
Plan also needs terraform and the Azure CLI installed.

rules.py     loads rule sentences (../ruleJsonFiles) and does retrieval
tasks.json   the Azure tasks
generate.py  python generate.py --models qwen3.8:27b --reps 3 --run examplerun
scan.py      python scan.py results/examplerun
main.py      original RAG proof of concept

Output ends up in results/<run>/.
