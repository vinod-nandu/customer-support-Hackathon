"""
Customer Support System — 3-Agent Sequential Pipeline (AutoGen AgentChat + Streamlit)

Agents (run in order via RoundRobinGroupChat):
  1. Assistant            -> answers from its own knowledge (no tools)
  2. WebSearchAssistant    -> searches the web, answers from results (web_search tool)
  3. EntryAgent            -> saves query + both answers + category to tickets.txt (save_ticket tool)

Extras on top of the base requirements:
  - Error handling around the whole team run (bad key, rate limit, network, empty
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

Run with:  streamlit run app.py

Required env vars:
  OPENAI_API_KEY   -> used by OpenAIChatCompletionClient

Install:
  pip install streamlit autogen-agentchat "autogen-ext[openai]" duckduckgo-search
"""

import asyncio
import datetime
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()  # reads OPENAI_API_KEY (and anything else) from a local .env file

import streamlit as st

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import TextMessage, ToolCallExecutionEvent
from autogen_ext.models.openai import OpenAIChatCompletionClient

TICKETS_FILE = "tickets.txt"
TICKET_DELIM = "=== TICKET"
VALID_CATEGORIES = ["billing", "technical", "general", "other"]
CATEGORY_COLORS = {
    "billing": "#f2a900",
    "technical": "#4c8bf5",
    "general": "#7ed957",
    "other": "#b0b0b0",
}

URL_RE = re.compile(r"https?://[^\s)>\]]+")


# --------------------------------------------------------------------------
# Tools (plain Python functions handed to specific agents only)
# --------------------------------------------------------------------------

def web_search(query: str) -> str:
    """Search the web for the given query and return a short text summary of
    the top results (title, snippet, url) that the agent can reason over.
    Guarded so a failed/rate-limited search never crashes the pipeline."""
    try:
        from ddgs import DDGS
    except ImportError:
        return "NO_RESULTS: 'duckduckgo_search' package is not installed."

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                title = r.get("title", "")
                body = r.get("body", "")
                href = r.get("href", "")
                if href:
                    results.append(f"- {title}: {body} (source: {href})")
        if not results:
            return "NO_RESULTS: the search returned nothing usable for this query."
        return "\n".join(results)
    except Exception as exc:  # keep the pipeline alive even if search fails
        return f"NO_RESULTS: web search failed ({exc})."


def save_ticket(query: str, assistant_answer: str, web_search_answer: str, category: str) -> str:
    """Append the query, both answers, and a category to tickets.txt as one
    ticket record. category must be one of: billing, technical, general, other."""
    category = category.strip().lower() if category else "general"
    if category not in VALID_CATEGORIES:
        category = "general"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"{TICKET_DELIM} [{timestamp}] ===\n"
        f"QUERY: {query}\n"
        f"CATEGORY: {category}\n"
        f"ASSISTANT_ANSWER: {assistant_answer}\n"
        f"WEB_SEARCH_ANSWER: {web_search_answer}\n\n"
    )
    with open(TICKETS_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    return f"Ticket saved successfully to tickets.txt with category '{category}'."


# --------------------------------------------------------------------------
# Agent / team construction
# --------------------------------------------------------------------------

def build_team() -> RoundRobinGroupChat:
    model_client = OpenAIChatCompletionClient(model="gpt-4o-mini")

    assistant_agent = AssistantAgent(
        name="Assistant",
        model_client=model_client,
        system_message=(
            "You are the first-line customer support Assistant. "
            "Answer the user's query directly using only your own knowledge. "
            "Do NOT use any tools. Be concise and clear. "
            "Do not say TERMINATE."
        ),
    )

    web_search_agent = AssistantAgent(
        name="WebSearchAssistant",
        model_client=model_client,
        tools=[web_search],
        system_message=(
            "You are the Web Search Assistant. Always call the web_search tool "
            "at least once with a query relevant to the user's original question. "
            "If the tool result starts with 'NO_RESULTS', clearly say the web "
            "search did not return usable results instead of making something up. "
            "Otherwise write a concise answer based on the search results, and "
            "keep the source URLs from the tool result visible in your answer. "
            "Do not say TERMINATE."
        ),
    )

    entry_agent = AssistantAgent(
        name="EntryAgent",
        model_client=model_client,
        tools=[save_ticket],
        system_message=(
            "You are the Entry Agent, the final step in the pipeline. "
            "From the conversation so far, identify: the original user query, "
            "the Assistant's answer, and the WebSearchAssistant's answer. "
            "Pick the single best category for this query: billing, technical, "
            "general, or other. "
            "Call the save_ticket tool exactly once with query, assistant_answer, "
            "web_search_answer, and category. "
            "Then reply to the user with a short message confirming both "
            "answers were recorded and which category was assigned, and finish "
            "your message with the single word TERMINATE."
        ),
    )

    termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(15)

    team = RoundRobinGroupChat(
        [assistant_agent, web_search_agent, entry_agent],
        termination_condition=termination,
    )
    return team


def extract_urls(text: str):
    return list(dict.fromkeys(URL_RE.findall(text or "")))


async def run_team_streaming(query: str, status_box, progress_lines: list):
    """Runs the team via run_stream(), updating a single st.status box live as
    each agent / tool call completes. Returns (assistant_answer, web_answer,
    source_urls, timings_dict)."""
    team = build_team()

    assistant_answer, web_answer = None, None
    source_urls = []
    timings = {}
    turn_start = time.time()
    current_source = None

    async for event in team.run_stream(task=query):
        source = getattr(event, "source", None)

        if source and source != current_source:
            if current_source is not None:
                timings[current_source] = round(time.time() - turn_start, 2)
            current_source = source
            turn_start = time.time()

            label = {
                "Assistant": "🧠 Assistant is answering from its own knowledge...",
                "WebSearchAssistant": "🌐 Web Search Assistant is searching the web...",
                "EntryAgent": "💾 Entry Agent is saving the ticket...",
            }.get(source, f"{source} is working...")
            progress_lines.append(label)
            status_box.update(label="\n".join(progress_lines))

        if isinstance(event, TextMessage):
            if event.source == "Assistant" and assistant_answer is None:
                assistant_answer = event.content
            elif event.source == "WebSearchAssistant" and web_answer is None:
                web_answer = event.content

        if isinstance(event, ToolCallExecutionEvent):
            for result in event.content:
                text = getattr(result, "content", "") or ""
                if "NO_RESULTS" not in text:
                    source_urls.extend(extract_urls(text))

    if current_source is not None:
        timings[current_source] = round(time.time() - turn_start, 2)

    source_urls = list(dict.fromkeys(source_urls))
    return (
        assistant_answer or "(no answer produced)",
        web_answer or "(no answer produced)",
        source_urls,
        timings,
    )


# --------------------------------------------------------------------------
# Ticket history (sidebar)
# --------------------------------------------------------------------------

def load_tickets():
    if not os.path.exists(TICKETS_FILE):
        return []
    with open(TICKETS_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    chunks = [c.strip() for c in raw.split(TICKET_DELIM) if c.strip()]
    tickets = []
    for chunk in chunks:
        ts_match = re.search(r"\[(.*?)\]", chunk)
        cat_match = re.search(r"CATEGORY:\s*(.*?)\n", chunk)
        q_match = re.search(r"QUERY:\s*(.*?)\n(?:CATEGORY:|ASSISTANT_ANSWER:)", chunk, re.S)
        a_match = re.search(r"ASSISTANT_ANSWER:\s*(.*?)\nWEB_SEARCH_ANSWER:", chunk, re.S)
        w_match = re.search(r"WEB_SEARCH_ANSWER:\s*(.*)", chunk, re.S)
        tickets.append({
            "timestamp": ts_match.group(1) if ts_match else "unknown",
            "category": (cat_match.group(1).strip() if cat_match else "general"),
            "query": q_match.group(1).strip() if q_match else "",
            "assistant_answer": a_match.group(1).strip() if a_match else "",
            "web_answer": w_match.group(1).strip() if w_match else "",
        })
    return list(reversed(tickets))  # newest first


def category_badge(category: str) -> str:
    color = CATEGORY_COLORS.get(category, CATEGORY_COLORS["other"])
    return (
        f"<span style='background-color:{color};color:white;padding:2px 8px;"
        f"border-radius:10px;font-size:0.75em;font-weight:600;'>{category}</span>"
    )


# --------------------------------------------------------------------------
# Streamlit UI
# --------------------------------------------------------------------------

st.set_page_config(page_title="Support Desk — 3-Agent AutoGen Pipeline", layout="wide")

with st.sidebar:
    st.header("🎫 Ticket History")

    col_search, col_reset = st.columns([3, 1])
    with col_search:
        search_term = st.text_input("Search past tickets", "", label_visibility="collapsed",
                                     placeholder="Search past tickets...")
    with col_reset:
        reset_clicked = st.button("🗑️ Reset", help="Clear all ticket history")

    if reset_clicked:
        if os.path.exists(TICKETS_FILE):
            os.remove(TICKETS_FILE)
        st.success("Ticket history cleared.")
        st.rerun()

    tickets = load_tickets()

    if os.path.exists(TICKETS_FILE):
        with open(TICKETS_FILE, "rb") as f:
            st.download_button(
                "⬇️ Download tickets.txt",
                data=f.read(),
                file_name="tickets.txt",
                mime="text/plain",
                use_container_width=True,
            )

    if search_term:
        tickets = [t for t in tickets if search_term.lower() in t["query"].lower()]

    st.caption(f"{len(tickets)} ticket(s) found" if search_term else f"{len(tickets)} total ticket(s)")

    for t in tickets:
        header = f"🕒 {t['timestamp']} — {t['query'][:35]}"
        with st.expander(header):
            st.markdown(category_badge(t["category"]), unsafe_allow_html=True)
            st.markdown(f"**Query:** {t['query']}")
            st.markdown(f"**Assistant Answer:** {t['assistant_answer']}")
            st.markdown(f"**Web Search Answer:** {t['web_answer']}")

st.title("🤖 Customer Support System")
st.caption("Assistant → Web Search Assistant → Entry Agent, run sequentially with AutoGen.")

if not os.environ.get("OPENAI_API_KEY"):
    st.warning("OPENAI_API_KEY is not set in the environment. Set it before running a query.")

query = st.text_input("Enter your query or support request:")
submit = st.button("Submit", type="primary")

if submit and not query.strip():
    st.error("Please enter a query before submitting.")

elif submit and query.strip():
    progress_lines: list = []
    status_box = st.status("Starting the agent pipeline...", expanded=True)

    try:
        assistant_answer, web_answer, source_urls, timings = asyncio.run(
            run_team_streaming(query.strip(), status_box, progress_lines)
        )
        status_box.update(label="✅ Pipeline finished.", state="complete", expanded=False)

    except Exception as exc:
        status_box.update(label="❌ Pipeline failed.", state="error", expanded=True)
        st.error(
            "Something went wrong while running the agent pipeline. "
            "This is usually a bad/missing OPENAI_API_KEY, a rate limit, or a "
            "network issue.\n\n"
            f"**Details:** `{exc}`"
        )
        st.stop()

    st.success("Done — ticket saved and answers ready.")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🧠 Assistant Answer")
        st.write(assistant_answer)
        if "Assistant" in timings:
            st.caption(f"⏱ {timings['Assistant']}s")
    with col2:
        st.subheader("🌐 Web Search Answer")
        st.write(web_answer)
        if source_urls:
            st.markdown("**Sources:**")
            for url in source_urls:
                st.markdown(f"- [{url}]({url})")
        if "WebSearchAssistant" in timings:
            st.caption(f"⏱ {timings['WebSearchAssistant']}s")

    if "EntryAgent" in timings:
        st.caption(f"💾 Ticket saved in {timings['EntryAgent']}s")

    st.info("This ticket has been saved and now appears in the Ticket History sidebar.")
