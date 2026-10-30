from __future__ import annotations

import base64
import html
import json
import math
import re
from html.parser import HTMLParser
from urllib.parse import urlparse


_ALLOWED_TAGS = {
    "p", "div", "br", "span", "strong", "b", "em", "i", "u", "s", "strike",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "blockquote", "a", "font", "hr",
    # Email-safe layout primitives used by the Professional Template Builder.
    "table", "tbody", "thead", "tfoot", "tr", "td", "th", "img",
}
_VOID_TAGS = {"br", "hr", "img"}
_ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "font": {"color", "face", "size"},
    "table": {"role", "width", "cellpadding", "cellspacing", "border"},
    "td": {"width", "height", "valign", "colspan", "rowspan"},
    "th": {"width", "height", "valign", "colspan", "rowspan"},
    "img": {"src", "alt", "width", "height", "title"},
    "*": {"style", "align", "class"},
}
_ALLOWED_ALIGN = {"left", "center", "right", "justify"}
_ALLOWED_TARGETS = {"_blank", "_self"}
_ALLOWED_CLASS_TOKENS = {
    "he-email-inner", "he-card-cell", "he-card-section-pad", "he-card-section-title",
    "he-stack-cell", "he-feature-cell", "he-feature-cell-one", "he-feature-cell-two", "he-grid-pad", "he-grid-title",
    "he-mobile-section-pad", "he-mobile-brand", "he-mobile-eyebrow",
    "he-mobile-hero-heading", "he-mobile-cta-heading", "he-mobile-copy",
    "he-mobile-rich", "he-mobile-button",
}
_DROP_CONTENT_TAGS = {"style", "script"}
_ALLOWED_CSS = {
    "color", "background-color", "font-family", "font-size", "font-weight",
    "font-style", "text-decoration", "text-align", "line-height",
    # Conservative email-layout CSS. Positioning, transforms and remote CSS URLs
    # remain deliberately unsupported.
    "width", "max-width", "height", "margin", "padding",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-radius", "vertical-align", "display", "border-collapse",
    # Safe responsive/wrapping properties emitted by the Professional Email Builder.
    "box-sizing", "table-layout", "white-space", "overflow", "text-overflow",
    "overflow-wrap", "word-break", "hyphens",
}
_SAFE_CSS_VALUE_RE = re.compile(r"^[#(),.%\-+\s\w'\"]{1,160}$")
_SIZE_RE = re.compile(r"^(?:[1-7]|(?:8|9|1\d|2\d|3[0-6])(?:px|pt)?|(?:0\.\d+|[1-3](?:\.\d+)?)?(?:em|rem)|(?:7[5-9]|[89]\d|1\d\d|200)%)$", re.I)
_COLOR_RE = re.compile(
    r"^(?:#[0-9a-f]{3,8}|rgb\([^)]{1,80}\)|rgba\([^)]{1,80}\)|hsl\([^)]{1,80}\)|hsla\([^)]{1,80}\)|[a-z]{1,24})$",
    re.I,
)


_BUILDER_COMMENT_RE = re.compile(r"^HE-BUILDER:[A-Za-z0-9_-]{1,180000}$")
_DIMENSION_ATTR_RE = re.compile(r"^(?:0|[1-9]\d{0,3}|100%)$")
_SPAN_ATTR_RE = re.compile(r"^[1-9]\d{0,2}$")

_BUILDER_ALLOWED_TYPES = {
    "header", "hero", "image", "richtext", "featuregrid", "offergrid", "promo",
    "cta", "social", "spacer", "divider", "footer",
    # Legacy V1.2 blocks are accepted so existing templates can be reopened and migrated.
    "cards2", "cards3", "services",
}
_BUILDER_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_BUILDER_DANGEROUS_KEYS = {"__proto__", "prototype", "constructor"}
_BUILDER_MAX_RAW_BYTES = 150_000
_BUILDER_MAX_STRING = 50_000
_BUILDER_MAX_LIST = 100
_BUILDER_MAX_DEPTH = 8


def _clean_builder_node(value, *, depth: int = 0):
    """Validate JSON-like HE-BUILDER metadata before preserving it in stored HTML."""
    if depth > _BUILDER_MAX_DEPTH:
        raise ValueError("Builder metadata is too deeply nested.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number) or abs(number) > 1_000_000_000:
            raise ValueError("Builder metadata contains an invalid number.")
        return value
    if isinstance(value, str):
        if len(value) > _BUILDER_MAX_STRING:
            raise ValueError("Builder metadata string is too large.")
        return value.replace("\x00", "")
    if isinstance(value, list):
        if len(value) > _BUILDER_MAX_LIST:
            raise ValueError("Builder metadata list is too large.")
        return [_clean_builder_node(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 80:
            raise ValueError("Builder metadata object is too large.")
        cleaned = {}
        for key, item in value.items():
            key = str(key or "")
            if key in _BUILDER_DANGEROUS_KEYS or not _BUILDER_KEY_RE.fullmatch(key):
                raise ValueError("Builder metadata contains an invalid key.")
            cleaned[key] = _clean_builder_node(item, depth=depth + 1)
        return cleaned
    raise ValueError("Builder metadata contains an unsupported value.")


def _sanitize_builder_comment(data: str) -> str:
    """Return a canonical safe HE-BUILDER comment or an empty string."""
    value = str(data or "").strip()
    if not _BUILDER_COMMENT_RE.fullmatch(value):
        return ""
    encoded = value.split(":", 1)[1]
    try:
        raw = encoded.replace("-", "+").replace("_", "/")
        raw += "=" * (-len(raw) % 4)
        decoded = base64.b64decode(raw, validate=True)
        if not decoded or len(decoded) > _BUILDER_MAX_RAW_BYTES:
            return ""
        parsed = json.loads(decoded.decode("utf-8"))
        if not isinstance(parsed, dict) or int(parsed.get("version") or 0) not in {1, 2}:
            return ""
        blocks = parsed.get("blocks")
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 60:
            return ""
        for block in blocks:
            if not isinstance(block, dict) or str(block.get("type") or "") not in _BUILDER_ALLOWED_TYPES:
                return ""
        cleaned = _clean_builder_node(parsed)
        canonical = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(canonical) > _BUILDER_MAX_RAW_BYTES:
            return ""
        safe_encoded = base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=")
        if len(safe_encoded) > 180_000:
            return ""
        return f"HE-BUILDER:{safe_encoded}"
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return ""


def _safe_href(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    # V1.3.8: allow only the controlled TERIQEN unsubscribe placeholder.
    # It is rendered to a signed HTTPS URL immediately before delivery.
    if re.fullmatch(r"{{\s*unsubscribe_url\s*}}", value, flags=re.IGNORECASE):
        return "{{unsubscribe_url}}"
    if value.startswith("#"):
        return value
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https", "mailto", "tel"}:
        return value
    return ""


def _safe_src(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""

    # TERIQEN Email Builder uploaded media.
    # Permit only the controlled Broadcast Media pseudo-URL.
    if re.fullmatch(
        r"local://broadcast_media/[A-Za-z0-9._-]+",
        value,
        flags=re.IGNORECASE,
    ):
        return value

    parsed = urlparse(value)
    if parsed.scheme.lower() == "https":
        return value

    return ""


def _clean_css(style: str) -> str:
    cleaned: list[str] = []
    for declaration in str(style or "").split(";"):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()
        if prop not in _ALLOWED_CSS or not value:
            continue
        lowered = value.lower().replace("\x00", "")
        if any(x in lowered for x in ("url(", "expression", "javascript:", "data:", "@import", "behavior:")):
            continue
        if not _SAFE_CSS_VALUE_RE.fullmatch(value):
            continue
        if prop in {"color", "background-color"} and not _COLOR_RE.fullmatch(value.replace(" ", "")):
            continue
        if prop == "font-size" and not _SIZE_RE.fullmatch(value.replace(" ", "")):
            continue
        if prop == "text-align" and value.lower() not in _ALLOWED_ALIGN:
            continue
        if prop == "font-weight" and value.lower() not in {"normal", "bold", "bolder", "lighter", "100", "200", "300", "400", "500", "600", "700", "800", "900"}:
            continue
        if prop == "font-style" and value.lower() not in {"normal", "italic", "oblique"}:
            continue
        if prop == "text-decoration" and not all(part in {"none", "underline", "line-through"} for part in value.lower().split()):
            continue
        if prop == "display" and value.lower() not in {"block", "inline", "inline-block", "table", "table-row", "table-cell", "none"}:
            continue
        if prop == "vertical-align" and value.lower() not in {"top", "middle", "bottom", "baseline"}:
            continue
        if prop == "border-collapse" and value.lower() not in {"collapse", "separate"}:
            continue
        if prop == "box-sizing" and value.lower() not in {"border-box", "content-box"}:
            continue
        if prop == "table-layout" and value.lower() not in {"auto", "fixed"}:
            continue
        if prop == "white-space" and value.lower() not in {"normal", "nowrap", "pre", "pre-wrap", "pre-line"}:
            continue
        if prop == "overflow" and value.lower() not in {"visible", "hidden", "auto", "scroll"}:
            continue
        if prop == "text-overflow" and value.lower() not in {"clip", "ellipsis"}:
            continue
        if prop == "overflow-wrap" and value.lower() not in {"normal", "break-word", "anywhere"}:
            continue
        if prop == "word-break" and value.lower() not in {"normal", "break-all", "keep-all", "break-word"}:
            continue
        if prop == "hyphens" and value.lower() not in {"none", "manual", "auto"}:
            continue
        cleaned.append(f"{prop}:{value}")
    return ";".join(cleaned)


class _EmailHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.drop_content_depth = 0

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = set(_ALLOWED_ATTRS.get("*", set())) | set(_ALLOWED_ATTRS.get(tag, set()))
        out: list[str] = []
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").lower().strip()
            if name not in allowed:
                continue
            value = str(raw_value or "").strip()
            if name == "href":
                value = _safe_href(value)
                if not value:
                    continue
            elif name == "src":
                value = _safe_src(value)
                if not value:
                    continue
            elif name == "style":
                value = _clean_css(value)
                if not value:
                    continue
            elif name == "align":
                value = value.lower()
                if value not in _ALLOWED_ALIGN:
                    continue
            elif name == "class":
                safe_tokens = [x for x in value.split() if x in _ALLOWED_CLASS_TOKENS]
                value = " ".join(dict.fromkeys(safe_tokens))
                if not value:
                    continue
            elif name == "target":
                value = value.lower()
                if value not in _ALLOWED_TARGETS:
                    continue
            elif name == "rel":
                safe_tokens = [x for x in value.lower().split() if x in {"noopener", "noreferrer", "nofollow"}]
                value = " ".join(dict.fromkeys(safe_tokens))
                if not value:
                    continue
            elif name == "color":
                if not _COLOR_RE.fullmatch(value.replace(" ", "")):
                    continue
            elif name == "size":
                if not re.fullmatch(r"[1-7]", value):
                    continue
            elif name == "face":
                if not re.fullmatch(r"[A-Za-z0-9 ,.'\-]{1,80}", value):
                    continue
            elif name == "title":
                value = value[:200]
            elif name == "alt":
                value = value[:300]
            elif name in {"width", "height"}:
                if not _DIMENSION_ATTR_RE.fullmatch(value):
                    continue
            elif name in {"colspan", "rowspan"}:
                if not _SPAN_ATTR_RE.fullmatch(value):
                    continue
            elif name == "valign":
                value = value.lower()
                if value not in {"top", "middle", "bottom", "baseline"}:
                    continue
            elif name == "role":
                value = value.lower()
                if value != "presentation":
                    continue
            elif name in {"cellpadding", "cellspacing", "border"}:
                if not re.fullmatch(r"\d{1,3}", value):
                    continue
            out.append(f' {name}="{html.escape(value, quote=True)}"')

        if tag == "a":
            names = {str(k or "").lower() for k, _ in attrs}
            target = next((str(v or "").lower() for k, v in attrs if str(k or "").lower() == "target"), "")
            if target == "_blank" and "rel" not in names:
                out.append(' rel="noopener noreferrer"')
        return "".join(out)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_content_depth:
            if tag in _DROP_CONTENT_TAGS:
                self.drop_content_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self.drop_content_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return
        self.parts.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_content_depth or tag in _DROP_CONTENT_TAGS:
            return
        if tag in _ALLOWED_TAGS:
            self.parts.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.drop_content_depth:
            if tag in _DROP_CONTENT_TAGS:
                self.drop_content_depth -= 1
            return
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.drop_content_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self.drop_content_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.drop_content_depth:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self.drop_content_depth:
            return
        # Preserve only the builder's URL-safe base64 metadata comment so a
        # saved professional template can be reopened block-by-block. Arbitrary
        # comments remain discarded.
        value = _sanitize_builder_comment(data)
        if value:
            self.parts.append(f"<!--{value}-->")

    def close(self) -> None:
        super().close()
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")


def sanitize_email_html(value: str, *, max_chars: int = 250_000) -> str:
    source = str(value or "")[:max_chars]
    parser = _EmailHTMLSanitizer()
    parser.feed(source)
    parser.close()
    return "".join(parser.parts).strip()


class _PlainTextExtractor(HTMLParser):
    _BLOCKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "blockquote", "hr", "table", "tr", "td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "li":
            self.parts.append("\n• ")
        elif tag.lower() in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_plain_text(value: str) -> str:
    parser = _PlainTextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    text = "".join(parser.parts).replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
