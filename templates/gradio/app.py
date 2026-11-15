"""A starter EFP app: a Gradio chat.

It shows the three things most apps need:

- who is visiting                         efp_app.integrations.gradio.get_visitor
- the owner's language model, streamed    efp_app.llm
- data that survives restarts             efp_app.storage

Path prefix: once deployed the app is served under /{slug}/. The host sets
GRADIO_SERVER_NAME, GRADIO_SERVER_PORT and GRADIO_ROOT_PATH, and launch()
reads them. So call demo.launch() without server_name, server_port or
root_path (and never with share=True): a value written here would replace
what the host set, and the app would not answer under /{slug}/.
"""

import gradio as gr

from efp_app import llm, storage
from efp_app.errors import BudgetExceededError, EfpError
from efp_app.integrations.gradio import get_visitor

SYSTEM_PROMPT = "You are a helpful assistant. Keep your answers short."


def text_of(content):
    """The text of a chat message, in any of the shapes Gradio versions use."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, (list, tuple)):
        return "".join(text_of(part) for part in content)
    return ""


def to_messages(history):
    """Gradio's chat history as the messages efp_app.llm takes."""
    messages = []
    for turn in history or []:
        if isinstance(turn, dict):  # {"role": ..., "content": ...}
            pairs = [(turn.get("role"), turn.get("content"))]
        elif isinstance(turn, (list, tuple)) and len(turn) == 2:  # [user, assistant] of older versions
            pairs = [("user", turn[0]), ("assistant", turn[1])]
        else:
            pairs = []
        for role, content in pairs:
            text = text_of(content)
            if role in ("user", "assistant") and text:
                messages.append({"role": role, "content": text})
    return messages


def note_key(visitor):
    """One note per signed-in visitor; anonymous visitors share one."""
    return "notes/%s.txt" % (visitor.id if visitor else "guest")


def greet(request: gr.Request):
    # Gradio fills in the request; the gateway signed the visitor into it.
    visitor = get_visitor(request)
    if visitor is None:
        return "Hello, guest. You are not signed in."
    return "Hello, %s%s." % (visitor.name, " (%s)" % visitor.group if visitor.group else "")


def reply(message, history, request: gr.Request):
    """Answer a chat message with the owner's model, piece by piece."""
    visitor = get_visitor(request)
    messages = to_messages(history) + [{"role": "user", "content": text_of(message)}]
    answer = ""
    try:
        # visitor= lets the Portal record who asked.
        for piece in llm.stream(messages, system=SYSTEM_PROMPT, visitor=visitor):
            answer += piece
            yield answer
    except BudgetExceededError as exc:
        # The app's daily budget is used up: say so instead of failing.
        yield (answer + "\n\n" if answer else "") + exc.user_message
    except EfpError as exc:
        # user_message is written for visitors; str(exc) has the detail for the log.
        print("LLM call failed:", exc)
        yield (answer + "\n\n" if answer else "") + exc.user_message


def load_note(request: gr.Request):
    try:
        return storage.load(note_key(get_visitor(request)), default=b"").decode("utf-8")
    except EfpError as exc:
        print("Loading the note failed:", exc)
        return ""


def save_note(text, request: gr.Request):
    try:
        storage.save(note_key(get_visitor(request)), text or "")
    except EfpError as exc:
        return exc.user_message
    return "Saved."


with gr.Blocks(title="My EFP app") as demo:
    gr.Markdown("# My EFP app")
    greeting = gr.Markdown()
    gr.ChatInterface(reply)
    with gr.Accordion("Your note", open=False):
        note = gr.Textbox(label="Kept on the server across restarts and deployments", lines=4)
        save = gr.Button("Save note")
        status = gr.Markdown()
    demo.load(greet, None, greeting)
    demo.load(load_note, None, note)
    save.click(save_note, note, status)


if __name__ == "__main__":
    # No server_name, server_port or root_path here: the host sets them
    # through the environment, and so does `efp-app dev`.
    demo.launch()
