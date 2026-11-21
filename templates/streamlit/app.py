"""A starter EFP app: Streamlit.

It shows the three things most apps need:

- who is visiting                         efp_app.integrations.streamlit.get_visitor
- the owner's language model, streamed    efp_app.llm
- data that survives restarts             efp_app.storage

Path prefix: once deployed the app is served under /{slug}/. The host starts
Streamlit with --server.baseUrlPath, so Streamlit serves its page, its files
and its WebSocket under the prefix by itself. The one thing to avoid is a
hand-written absolute link, such as st.markdown("[Home](/)") or
<a href="/..."> in HTML: write links relative to the page, or build them
from efp_app.app.base_path().
"""

import streamlit as st

from efp_app import llm, storage
from efp_app.errors import BudgetExceededError, EfpError
from efp_app.integrations.streamlit import get_visitor

SYSTEM_PROMPT = "You are a helpful assistant. Keep your answers short."
# Every call is paid from the app's daily budget, whoever asks: both ends of it are capped.
MAX_QUESTION_CHARS = 8000
MAX_OUTPUT_TOKENS = 1000
MAX_MESSAGES = 21  # what goes to the model: the question and the ten exchanges before it


def recent(messages):
    """The end of a conversation: what goes to the model with a question.

    Each question sends the conversation again, so a long chat would cost
    more with every turn. Only the newest messages go, each cut to
    MAX_QUESTION_CHARS, and the first of them is the user's: a conversation
    starts with a question.
    """
    kept = [
        {"role": message["role"], "content": message["content"][:MAX_QUESTION_CHARS]}
        for message in messages[-MAX_MESSAGES:]
    ]
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


st.set_page_config(page_title="My EFP app")

# The gateway signs the visitor into the request that opens the session.
visitor = get_visitor()

st.title("My EFP app")
if visitor:
    st.write("Hello, %s%s." % (visitor.name, " (%s)" % visitor.group if visitor.group else ""))
else:
    st.write("Hello, guest. You are not signed in.")


# --- A note that is kept on the server ----------------------------------------

note_key = "notes/%s.txt" % (visitor.id if visitor else "guest")

with st.sidebar:
    st.subheader("Your note")
    if "note" not in st.session_state:
        try:
            st.session_state.note = storage.load(note_key, default=b"").decode("utf-8")
        except EfpError as exc:
            st.session_state.note = ""
            st.error(exc.user_message)
    note = st.text_area("Kept across restarts and deployments", key="note", height=160)
    if st.button("Save note"):
        try:
            storage.save(note_key, note)
            st.success("Saved.")
        except EfpError as exc:
            st.error(exc.user_message)


# --- A chat with the owner's model ---------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Ask something", max_chars=MAX_QUESTION_CHARS)
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            # visitor= lets the Portal record who asked.
            stream = llm.stream(
                recent(st.session_state.messages),
                system=SYSTEM_PROMPT,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                visitor=visitor,
            )
            answer = st.write_stream(stream)
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except BudgetExceededError as exc:
            # The app's daily budget is used up: say so instead of failing.
            st.warning(exc.user_message)
        except EfpError as exc:
            # user_message is written for visitors; str(exc) has the detail for the log.
            print("LLM call failed:", exc)
            st.error(exc.user_message)
