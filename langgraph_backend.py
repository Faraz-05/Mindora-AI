import uuid
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from prompts import *
from langchain.messages import RemoveMessage
from langchain_core.messages.utils import trim_messages, count_tokens_approximately
from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph.message import add_messages
from typing import List, TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver 
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool
from dotenv import load_dotenv
import os
import sqlite3
import json
import requests
from streamlit import cursor

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

############# Tools ###########################
# tools
search_tool = DuckDuckGoSearchRun(region="us-en")

@tool
def calculator(first_num: float, second_num: float, operation: str) -> dict:
    """
    A simple calculator tool that can perform basic arithmetic operations.
    Allowable operations are: add, subtract, multiply, divide.
    """

    if operation == "add":
        result = first_num + second_num
    elif operation == "subtract":
        result = first_num - second_num
    elif operation == "multiply":
        result = first_num * second_num
    elif operation == "divide":
        result = first_num / second_num
    else:
        return {"error": "Invalid operation. Please choose from add, subtract, multiply, divide."}

    return {"first_num": first_num, "second_num": second_num, "operation": operation, "result": result}

@tool
def get_stock_price(symbol: str) -> dict:
    """
    Fetch latest stock price for a given symbol (e.g. 'AAPL', 'TSLA') 
    using Alpha Vantage with API key in the URL.
    """

    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={os.getenv('ALPHA_VANTAGE_API_KEY')}"
    r = requests.get(url)
    return r.json()

tools = [search_tool, calculator, get_stock_price]
llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", api_key=api_key)
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

class MemoryItem(BaseModel):
    text: str = Field(description="Atomic user memory")
    is_new: bool = Field(description="True if new, false if duplicate")

class MemoryDecision(BaseModel):
    should_write: bool
    memories: List[MemoryItem] = Field(default_factory=list)

memory_extractor = llm.with_structured_output(MemoryDecision)

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_id: str
    title_generated: bool
    summary: str


def remember_node(state: ChatState, config: RunnableConfig):
    user_id = config["configurable"]["user_id"]
    ns = ("user", user_id, "details")

    # existing memory (all items under namespace)
    items = get_all_memory(ns)
    existing = "\n".join(it[1].get("data", "") for it in items) if items else "(empty)"

    # latest user message
    last_text = state["messages"][-1].content

    decision: MemoryDecision = memory_extractor.invoke(
        [
            SystemMessage(content=MEMORY_PROMPT.format(user_details_content=existing)),
            {"role": "user", "content": last_text},
        ]
    )

    if decision.should_write:
        for mem in decision.memories:
            if mem.is_new and mem.text.strip():
                print(f"Storing new memory: {mem.text.strip()}")
                insert_memory(ns, str(uuid.uuid4()), {"data": mem.text.strip()})

    return {}

def chat_node(state: ChatState, config: RunnableConfig) -> ChatState:
    messages = []

    user_id = config["configurable"]["user_id"]
    ns = ("user", user_id, "details")
    long_term_memory_items = get_all_memory(ns)
    user_details_content = "\n".join(it[1].get("data", "") for it in long_term_memory_items) if long_term_memory_items else "(empty)"
    system_message = SystemMessage(content=SYSTEM_PROMPT_TEMPLATE.format(user_details_content=user_details_content))

    if state.get('summary'):
        messages.append({
            "role": "system", 
            "content": f"Conversation summary: {state['summary']}"
        })
    messages.extend(state['messages'])
    print(messages)
    # messages = trim_messages(
    #     state['messages'], 
    #     max_tokens=36000, 
    #     strategy="last",
    #     start_on="human",
    #     include_system=True,
    #     token_counter=count_tokens_approximately
    #     )

    # print("token count:", count_tokens_approximately(messages))
    # for msg in messages:
    #     print(msg)

    response = llm_with_tools.invoke([system_message] + messages)
    return {'messages': [response]}

def summarize_conversation(state: ChatState) -> ChatState:
    if not should_summarize(state):
        return state  # No summarization needed

    existing_summary = state.get('summary', '')
    if existing_summary:
        prompt = f"Existing summary: {existing_summary}\n\nExtend the summary using the new conversation above.\n\n"
    else:
        prompt = "Create a summary of the conversation above.\n\n"      

    messages_for_summary = state['messages'] + [HumanMessage(content=prompt)]
    response = llm.invoke(messages_for_summary)
    messages_to_delete = state['messages'][:-10] # Keep the last 10 messages for context

    return {
        'summary': response.content[0]['text'].strip(),
        'messages': [RemoveMessage(id=msg.id) for msg in messages_to_delete]  # Clear messages after summarization
    }

def should_summarize(state: ChatState):
    return len(state["messages"]) > 20 # Summarize if there are more than 20 messages

def title_generation_node(state: ChatState) -> ChatState:
    """
    Generate title on first user message if not already generated.
    This node runs before the chat node to ensure title is generated early.
    """
    messages = state['messages']
    conversation_id = state.get('conversation_id', '')
    title_generated = state.get('title_generated', False)
    
    # Only generate if we haven't generated yet and we have a conversation_id
    if not title_generated and conversation_id and len(messages) > 0:
        # Find first human message
        first_user_message = None
        for msg in messages:
            if isinstance(msg, HumanMessage):
                first_user_message = msg.content
                break
        
        # Generate title if we found a user message and conversation doesn't already have one
        if first_user_message and not conversation_has_title(conversation_id):
            title = generate_conversation_title(first_user_message)
            update_conversation_title(conversation_id, title)
            
            return {
                'messages': state['messages'],
                'conversation_id': conversation_id,
                'title_generated': True
            }
    
    return state

def retrieve_all_threads():
    all_threads = set()
    for checkpoint in checkpointer.list(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    
    return list(all_threads)

conn = sqlite3.connect('chatbot_1.db', check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)

# ***************************** Conversations Database *****************************
def init_memory_db():
    """Initialize memory table if not exists."""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memory (
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (namespace, key)
        )
    ''')
    conn.commit()

def _to_json_text(value):
    """Serialize JSON-like values for SQLite storage, including tuples."""
    if isinstance(value, tuple):
        payload = {"__type__": "tuple", "value": list(value)}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

def _from_json_text(value):
    """Deserialize stored JSON text back into Python objects."""
    if value is None:
        return None

    decoded = json.loads(value)
    if isinstance(decoded, dict) and decoded.get("__type__") == "tuple":
        return tuple(decoded.get("value", []))
    return decoded

def insert_memory(namespace, key, value):
    """Insert a memory into the memory table."""
    cursor = conn.cursor()
    namespace_json = _to_json_text(namespace)
    value_json = _to_json_text(value)
    cursor.execute('''
        INSERT INTO memory (namespace, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(namespace, key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
    ''', (namespace_json, key, value_json))
    conn.commit()

def get_memory(namespace, key):
    """Retrieve a memory from the memory table."""
    cursor = conn.cursor()
    namespace_json = _to_json_text(namespace)
    cursor.execute('''
        SELECT value
        FROM memory
        WHERE namespace = ? AND key = ?
    ''', (namespace_json, key))
    result = cursor.fetchone()
    if not result:
        return None
    return _from_json_text(result[0])

def get_all_memory(namespace):
    """Retrieve all memory entries for a given namespace."""
    cursor = conn.cursor()
    namespace_json = _to_json_text(namespace)
    cursor.execute('''
        SELECT key, value
        FROM memory
        WHERE namespace = ?
    ''', (namespace_json,))
    rows = cursor.fetchall()
    return [(key, _from_json_text(value)) for key, value in rows]

def init_messages_db():
    """Initialize messages table if not exists."""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            conversation_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (conversation_id, sequence)
        )
    ''')
    conn.commit()
            
def insert_message(conversation_id, role, content):
    """Insert a message into the messages table."""
    cursor = conn.cursor()

    # step 1: find latest sequence number for this conversation
    cursor.execute("""
        SELECT MAX(sequence)
        FROM messages
        WHERE conversation_id = ?
    """, (conversation_id,))

    latest_sequence = cursor.fetchone()[0]

    # step 2: calculate the next sequence number
    sequence = latest_sequence + 1 if latest_sequence is not None else 1

    # step 3: insert the new message with the next sequence number
    cursor.execute('''
        INSERT INTO messages (conversation_id, sequence, role, content)
        VALUES (?, ?, ?, ?)
    ''', (conversation_id, sequence, role, content))
    conn.commit()

def get_messages_for_conversation(conversation_id):
    """Retrieve all messages for a given conversation_id."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY sequence ASC
    ''', (conversation_id,))
    return cursor.fetchall()

def init_conversation_db():
    """Initialize conversations table if not exists."""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()

def create_conversation(conversation_id):
    """Create a new conversation with null title."""
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (conversation_id, title)
            VALUES (?, NULL)
        ''', (conversation_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass

def update_conversation_title(conversation_id, title):
    """Update conversation title."""
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE conversations
        SET title = ?, updated_at = CURRENT_TIMESTAMP
        WHERE conversation_id = ?
    ''', (title, conversation_id))
    conn.commit()

def get_conversation(conversation_id):
    """Get conversation metadata."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT conversation_id, title, created_at, updated_at
        FROM conversations
        WHERE conversation_id = ?
    ''', (conversation_id,))
    return cursor.fetchone()

def get_all_conversations():
    """Get all conversations sorted by most recent."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT conversation_id, title, created_at, updated_at
        FROM conversations
        ORDER BY updated_at DESC
    ''')
    return cursor.fetchall()

def conversation_has_title(conversation_id):
    """Check if conversation already has a title."""
    conv = get_conversation(conversation_id)
    return conv is not None and conv[1] is not None

# ***************************** Title Generation Service *****************************
def generate_conversation_title(user_message: str) -> str:
    """
    Generate a concise conversation title from the first user message.
    
    Args:
        user_message: The first user message
        
    Returns:
        Generated title (3-8 words) or fallback title
    """
    if not user_message or len(user_message.strip()) == 0:
        return "New Resume Chat"
    
    system_prompt = """You are a title generation assistant for a resume review chatbot.
Generate a concise, descriptive conversation title based on the user's message.

Rules:
- Maximum 6 words
- Do not use quotation marks
- Do not include punctuation unless necessary (hyphens OK)
- Return only the title, nothing else
- Make it descriptive and easy to scan in a sidebar
- Focus on the key topic or role mentioned

Examples:
- "Google SWE Resume Review" (from "Review my resume for Google SWE roles")
- "ATS Optimization Help" (from "Help me improve ATS score")
- "Flutter Developer Analysis" (from "Analyze my Flutter developer resume")"""
    
    try:
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ])
        
        title = response.content[0]['text'].strip()
        
        if not title or title == "":
            return "New Resume Chat"
        
        # Validate title length - keep max 8 words
        words = title.split()
        if len(words) > 8:
            title = " ".join(words[:6])
        
        return title
    
    except Exception as e:
        print(f"Error generating title: {e}")
        return "New Resume Chat"

# Initialize database
init_conversation_db()
init_messages_db()
init_memory_db()

graph = StateGraph(ChatState)
graph.add_node('remember_node', remember_node)
graph.add_node('title_generation', title_generation_node)
graph.add_node('chat_node', chat_node)
graph.add_node('tools', tool_node)
graph.add_node('summarize', summarize_conversation)
graph.add_edge(START, 'remember_node')
graph.add_edge('remember_node', 'title_generation')
graph.add_edge('title_generation', 'chat_node')
graph.add_conditional_edges('chat_node', tools_condition)
graph.add_edge('tools', 'chat_node')
graph.add_edge('chat_node', 'summarize')

chatbot = graph.compile(checkpointer=checkpointer)
# display the graph as a mermaid diagram
with open("workflow.png", "wb") as f:
    f.write(chatbot.get_graph().draw_mermaid_png())
