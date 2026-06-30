# Looker MCP Agent

An AI Agent built with the Google Agent Development Kit (ADK) that integrates with Model Context Protocol (MCP) server endpoints and Gmail API to query Looker data and compose/send summary emails.

## Repository Structure

```
.
├── .agent_engine_config.json   # Agent Engine Private Service Connect (PSC) config
├── README.md                   # Project documentation
├── .gitignore                  # Git ignore rules
└── looker-agent-email2/        # Main agent package
    ├── agent.py                # Agent implementation & tool setup
    ├── requirements.txt        # Dependencies
    ├── .env.example            # Environment variables template
    ├── .adkignore              # ADK deployment ignore rules
    ├── .gcloudignore           # GCloud deployment ignore rules
    └── .gitignore              # Package-specific git ignore rules
```

## Setup & Prerequisites

### 1. Requirements
- Python 3.10+
- Access to Google Cloud Vertex AI / Agent Engine
- An active Looker MCP server endpoint

### 2. Installation
Navigate into the agent directory and install dependencies:

```bash
cd looker-agent-email2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Variables
Copy `.env.example` to `.env` in `looker-agent-email2/` and update the values:

```bash
cp looker-agent-email2/.env.example looker-agent-email2/.env
```

### 4. Running the Agent
To run locally or test the agent:

```bash
python looker-agent-email2/agent.py
```
