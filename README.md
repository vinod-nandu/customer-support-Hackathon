
# AutoGen Customer Support System

## Overview

This project implements an e-commerce customer support workflow using:

- Microsoft AutoGen AgentChat API
- AssistantAgent
- RoundRobinGroupChat
- Streamlit

## Agent Flow

```
User Query
    |
    v
Assistant Agent
(no tools, own knowledge)
    |
    v
Web Search Assistant
(web_search tool only)
    |
    v
Entry Agent
(save_to_file tool only)
    |
    v
Streamlit displays both answers
```

## Agents

### 1. Assistant

Purpose:
- Answers customer questions directly.
- Has no tools.

### 2. Web Search Assistant

Purpose:
- Uses only the web_search tool.
- Searches and creates an answer from results.

### 3. Entry Agent

Purpose:
- Reads previous conversation messages.
- Uses only save_to_file.
- Stores query, answer 1, and answer 2.
- Returns both answers.

## Environment Setup

Create an environment variable:

```
OPENAI_API_KEY=your_key_here
```

Do not place API keys inside source code.

## Install

```
pip install streamlit autogen-agentchat autogen-ext
```

## Run

```
streamlit run app.py
```

## Notes

The included web_search function is a placeholder.
Replace it with a real search provider API implementation.

The AutoGen team uses RoundRobinGroupChat so agents execute in a fixed order.
The Streamlit button uses asyncio.run() because AutoGen execution is asynchronous.

Customer Support System — 3-Agent Sequential Pipeline (AutoGen AgentChat + Streamlit)
Agents (run in order via RoundRobinGroupChat):
1. Assistant -> answers from its own knowledge (no tools)
2. WebSearchAssistant -> searches the web, answers from results (web_search tool)
3. EntryAgent -> saves query + both answers + category to tickets.txt (save_ticket tool)
4. - Error handling around the whole team run (bad key, rate limit, network, empty
output) so the app shows a clean st.error instead of crashing mid-demo.
- Live per-agent progress using run_stream() + st.status(...) instead of one
big spinner, so the 3-agent handoff is visible as it happens.
- Source links extracted from the web_search tool's own output and rendered
as clickable links under the Web Search Answer.
- Auto-categorized tickets (billing / technical / general / other), shown as
a colored badge in the sidebar ticket history.
- Per-agent latency shown under the results.
- Download button for the raw tickets.txt file.
- Clear / reset ticket history button in the sidebar.
- Web search failures are caught and reported as "no results" instead of
breaking the pipeline or getting saved as garbage.
Deployment architecture — the browser hits nginx, which proxies into the Docker container running Streamlit on port 8501. From inside that container, the app calls out to the OpenAI API (for all three agents' LLM calls) and DuckDuckGo (for the web-search tool), and writes ticket records to the ./data volume so tickets.txt survives container restarts.
Agent orchestration — inside RoundRobinGroupChat, the three agents fire in a strict fixed order and share the same conversation history: Assistant answers first from memory, WebSearchAssistant calls web_search() and answers from results, then EntryAgent calls save_ticket() with both prior answers and a category. Once EntryAgent says "TERMINATE" (or the 15-message safety cap hits), the team stops and Streamlit extracts both TextMessage answers to display.
