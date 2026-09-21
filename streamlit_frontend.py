import streamlit as st
from langgraph_backend import (
    chatbot, retrieve_all_threads, create_conversation,
    get_conversation, get_all_conversations, insert_message, get_messages_for_conversation
)
from langchain_core.messages import HumanMessage, AIMessage
import uuid



# ************************************* Utility Functions ********************************
def generate_thread_id():
    thread_id = str(uuid.uuid4())
    return thread_id

def reset_chat():
    """Create new conversation with no title initially."""
    thread_id = generate_thread_id()
    create_conversation(thread_id)
    st.session_state["thread_id"] = thread_id
    st.session_state["title_generated"] = False
    add_thread(thread_id)
    st.session_state["message_history"] = []

def get_conversation_display_name(thread_id):
    """Get title for display, fallback to shortened ID if not generated yet."""
    conv = get_conversation(thread_id)
    if conv and conv[1]:
        return conv[1]
    return f"Chat {thread_id[:8]}"

def add_thread(thread_id):
    if thread_id not in st.session_state["chat_threads"]:
        st.session_state["chat_threads"].append(thread_id)

def load_conversation(thread_id):
    messages = get_messages_for_conversation(thread_id)
    return messages if messages else []

# ************************************* Session Setup ************************************
if "message_history" not in st.session_state:
    st.session_state["message_history"] = []

if "thread_id" not in st.session_state:
    thread_id = generate_thread_id()
    create_conversation(thread_id)
    st.session_state["thread_id"] = thread_id
    st.session_state["title_generated"] = False

if "chat_threads" not in st.session_state:
    st.session_state["chat_threads"] = retrieve_all_threads()

add_thread(st.session_state["thread_id"])

# ************************************** Sidebar UI **************************************
st.sidebar.title("Resume Chat")
if st.sidebar.button("➕ New Chat"):
    reset_chat()
    st.rerun()

st.sidebar.header("Recent Chats")

all_conversations = get_all_conversations()

if all_conversations:
    for conv_id, title, created, updated in all_conversations:
        display_name = title if title else f"Chat {conv_id[:8]}"
        is_active = conv_id == st.session_state["thread_id"]
        
        button_label = ("▶ " if is_active else "  ") + display_name
        
        if st.sidebar.button(button_label, key=f"btn_{conv_id}", use_container_width=True):
            st.session_state["thread_id"] = conv_id
            messages = load_conversation(conv_id)
            temp_messages = []
            for message in messages:
                role, content = message
                if role == "user":
                    temp_messages.append({"role": "user", "content": content})
                elif role == "assistant":
                    temp_messages.append({"role": "assistant", "content": content})
            st.session_state["message_history"] = temp_messages
            st.rerun()

# ************************************** Main UI *****************************************
config = {
    'configurable': {'thread_id': st.session_state["thread_id"], 'user_id': 'u1'},
    'metadata': {
        'thread_id': st.session_state["thread_id"],
    },
    'run_name': 'chat_turn'
}

for message in st.session_state["message_history"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_input = st.chat_input("Type here...")

if user_input:
    insert_message(st.session_state["thread_id"], "user", user_input)
    st.session_state["message_history"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.text(user_input)

    with st.chat_message("assistant"):
        def ai_only_stream():
            for chat_message, metadata in chatbot.stream(
                {
                    'messages': [HumanMessage(content=user_input)],
                    'conversation_id': st.session_state["thread_id"],
                    'title_generated': st.session_state.get("title_generated", False),
                    # 'summary': '',
                },
                config=config,
                stream_mode='messages',
            ):
                if isinstance(chat_message, AIMessage) and metadata.get('langgraph_node') == 'chat_node':
                    content = chat_message.content
                    if isinstance(content, list) and len(content) > 0:
                        text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
                        yield text
                    elif isinstance(content, str):
                        yield content
        
        ai_response = st.write_stream(ai_only_stream())
        st.session_state["message_history"].append({"role": "assistant", "content": ai_response})
        insert_message(st.session_state["thread_id"], "assistant", ai_response)

        # Update title_generated flag if first message was just processed
        if not st.session_state.get("title_generated", False):
            st.session_state["title_generated"] = True

