from .html_source import HtmlSource
from .email_source import EmailSource

REGISTRY = {
    "html": HtmlSource,
    "email": EmailSource,
}


def build(source_cfg: dict, ctx):
    kind = source_cfg.get("type", "html")
    if kind not in REGISTRY:
        raise ValueError(f"Tipo di fonte sconosciuto: {kind}")
    return REGISTRY[kind](source_cfg, ctx)
