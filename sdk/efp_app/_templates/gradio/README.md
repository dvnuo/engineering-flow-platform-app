# Gradio starter

One script (`app.py`): a chat with the owner's language model that greets the
visitor, and a note kept with `efp_app.storage`.

## Run it locally

```
pip install efp-app-sdk -r requirements.txt
copy .env.example .env        (cp on Linux and macOS), then fill it in
efp-app dev
```

Open http://127.0.0.1:8000/.

## Deploy it

1. Put your Portal username in `"owners"` of `efp-app.json`.
2. `efp-app check`
3. Push the code to GitHub, register the repository as an app in the Portal
   and press Deploy.

## The path prefix

Deployed, the app lives at `https://<apps host>/<slug>/`. The host tells
Gradio where it runs through `GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT` and
`GRADIO_ROOT_PATH`, and `demo.launch()` reads them.

The one mistake to avoid is passing those values yourself:
`demo.launch(server_port=7860)`, `demo.launch(root_path="/")` or
`share=True` replace what the host set, and the app does not answer under
its address. Keep `demo.launch()` without arguments, inside
`if __name__ == "__main__":`.

## Good to know

- An event function gets the visitor from its `gr.Request` parameter
  (`get_visitor(request)`); there is no global "current visitor".
- The app stops when nobody has used it for a while and starts again on the
  next visit: keep nothing important in memory, use `efp_app.storage`.
