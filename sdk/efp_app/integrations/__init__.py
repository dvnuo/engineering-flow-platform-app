"""Helpers that find the visitor in a web framework's request.

    efp_app.integrations.fastapi     get_visitor / require_visitor as dependencies
    efp_app.integrations.flask       get_visitor() / require_visitor() inside a request
    efp_app.integrations.streamlit   get_visitor() inside a script run
    efp_app.integrations.gradio      get_visitor(request) with a gr.Request

Each module imports its framework only when it is used, so the SDK works
without any of them installed. All of them end in ``efp_app.visitor.current``.
"""
