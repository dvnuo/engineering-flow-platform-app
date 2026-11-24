"""Questions about images, answered by a vision model through the Portal.

docs/CONTRACTS.md, section 9.4.

    from efp_app import images

    answer = images.inspect(screenshot, "Which field is marked as wrong?", max_output_tokens=600, visitor=who)
    print(answer.text, answer.usage.input_tokens)

    images.inspect([before, after], "What differs between the two screenshots?")

An image is its bytes. The SDK tells JPEG, PNG, WebP and GIF apart by their
first bytes; a pair ``(data, media_type)`` says in addition what the app
believes the image to be, and is refused when the bytes say otherwise. One
call takes one to four images and one question, and the answer comes whole:
there is no stream and there are no tool calls. ``ainspect`` is the same for
asynchronous code.

The model is the one of the owner's Image analysis connector, which the
owner turns on for the app in the Portal (``efp_app.app.info().image_analysis``
says whether it is on). A call counts against the app's daily budget like a
call of ``efp_app.llm``, which itself stays text only; pass ``visitor=`` (a
``Visitor`` or the ``X-EFP-Visitor`` value) so the Portal records who caused
the call.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from . import client as _client
from . import errors
from . import llm as _llm
from .llm import Usage

#: The most images one call takes.
MAX_IMAGES = 4
#: The most bytes one image holds: 5 MiB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: The most bytes the images of one call hold together: 20 MiB.
MAX_TOTAL_BYTES = 20 * 1024 * 1024
#: The kinds of image the model is given.
MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
#: Seconds an image call may take (the longest wait for the Portal, as for a model call).
INSPECT_TIMEOUT = 600.0
_PATH = "/images/inspect"

#: One image: its bytes, or the bytes with the media type the app believes they have.
Image = Union[bytes, bytearray, Tuple[Union[bytes, bytearray], str]]
#: The first argument of a call: one image or a sequence of them.
Images = Union[Image, Sequence[Image]]

_BYTES = (bytes, bytearray)
_NAMES = "JPEG, PNG, WebP or GIF"


@dataclass(frozen=True)
class Inspection:
    text: str
    #: The model of the owner's Image analysis connector.
    model: Optional[str] = None
    #: ``stop`` or ``length`` (the answer hit ``max_output_tokens``).
    finish_reason: Optional[str] = None
    usage: Usage = field(default_factory=Usage)

    def __str__(self) -> str:
        return self.text


# --- Request and answer ------------------------------------------------------


def media_type_of(data: Union[bytes, bytearray]) -> Optional[str]:
    """The media type the first bytes of ``data`` give it away as, or ``None``.

    One of ``MEDIA_TYPES``. ``None`` for bytes that are none of the four, and
    for a value that is not bytes at all. Only the first twelve bytes are
    looked at: the rest of the file is not checked, here or by the Portal.
    """
    if not isinstance(data, _BYTES):
        return None
    head = bytes(data[:12])
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _is_pair(value: Any) -> bool:
    """True for ``(data, media_type)``. Two images in a tuple are not one: an image is never a string."""
    return (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and isinstance(value[0], _BYTES)
        and isinstance(value[1], str)
    )


def _listed(images: Any) -> List[Any]:
    if isinstance(images, _BYTES) or _is_pair(images):
        return [images]
    if isinstance(images, (str, Mapping)):
        # Both can be iterated, and neither is ever a sequence of images: say what is wanted
        # instead of complaining about the first character of a path.
        raise errors.InvalidRequestError(
            "images must be the bytes of an image, a pair (bytes, media type), or a sequence of those; "
            "a path, a URL or base64 text is not an image"
        )
    try:
        return list(images)
    except TypeError:
        raise errors.InvalidRequestError(
            "images must be the bytes of an image, a pair (bytes, media type), or a sequence of those"
        ) from None


def _checked(images: Any) -> List[Tuple[str, Union[bytes, bytearray]]]:
    """The rules of section 9.4, checked before anything is encoded or sent.

    Each image comes back with the media type its bytes have. The Portal
    checks all of this again; here a mistake costs no request and nothing of
    the budget, and the message says which image it is about.
    """
    items = _listed(images)
    if not items:
        raise errors.InvalidRequestError("there is no image to look at")
    if len(items) > MAX_IMAGES:
        raise errors.InvalidRequestError("one call takes at most %d images, got %d" % (MAX_IMAGES, len(items)))
    found: List[Tuple[str, Union[bytes, bytearray]]] = []
    total = 0
    for number, item in enumerate(items, 1):
        declared = None
        if _is_pair(item):
            item, declared = item
        if not isinstance(item, _BYTES):
            raise errors.InvalidRequestError(
                "image %d must be bytes, or a pair (bytes, media type); got %s" % (number, type(item).__name__)
            )
        size = len(item)
        if size == 0:
            raise errors.InvalidRequestError("image %d is empty" % number)
        if size > MAX_IMAGE_BYTES:
            raise errors.InvalidRequestError(
                "image %d holds %d bytes; one image holds at most %d" % (number, size, MAX_IMAGE_BYTES)
            )
        total += size
        if total > MAX_TOTAL_BYTES:
            raise errors.InvalidRequestError(
                "the images hold more than %d bytes together, which is the most one call takes" % MAX_TOTAL_BYTES
            )
        kind = media_type_of(item)
        if kind is None:
            raise errors.InvalidRequestError(
                "image %d is not a %s image, going by its first bytes" % (number, _NAMES)
            )
        if declared is not None:
            # Letter case and blanks do not tell two media types apart, so they do not make one wrong.
            named = declared.strip().lower()
            if named not in MEDIA_TYPES:
                raise errors.InvalidRequestError(
                    "the media type of image %d must be one of %s; got %r"
                    % (number, ", ".join(MEDIA_TYPES), declared[:60])
                )
            if named != kind:
                raise errors.InvalidRequestError(
                    "image %d is given as %s, but its first bytes are those of %s" % (number, named, kind)
                )
        found.append((kind, item))
    return found


def _body(
    images: Any,
    prompt: Any,
    system: Any,
    max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
) -> Dict[str, Any]:
    _llm._check_reasoning_effort(reasoning_effort)
    checked = _checked(images)
    if not isinstance(prompt, str) or not prompt.strip():
        raise errors.InvalidRequestError("the prompt must be a string that is not blank: the question about the images")
    if system is not None and not isinstance(system, str):
        raise errors.InvalidRequestError("system must be a string, or None")
    body: Dict[str, Any] = {
        "images": [
            {"media_type": kind, "data_base64": base64.b64encode(data).decode("ascii")} for kind, data in checked
        ],
        "prompt": prompt,
        "system": system,
    }
    # The contract gives both a meaning when they are left out (4,096 tokens; "low"), and does not
    # say that null means the same, so what the call did not name is not sent at all.
    if max_output_tokens is not None:
        body["max_output_tokens"] = max_output_tokens
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return body


def _inspection(payload: Any) -> Inspection:
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise errors.UnexpectedResponseError("The Portal's answer to an image call has no text.")
    return Inspection(
        text=payload["text"],
        model=payload.get("model"),
        finish_reason=payload.get("finish_reason"),
        usage=_llm._usage(payload.get("usage")),
    )


# --- The calls ---------------------------------------------------------------


def inspect(
    images: Images,
    prompt: str,
    *,
    system: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> Inspection:
    """Ask one question about one to four images and wait for the whole answer.

    ``images`` is one image or a sequence of them; an image is ``bytes``, a
    ``bytearray``, or a pair ``(data, media_type)``. ``prompt`` is the
    question; the model gets it first, then the images in the order given,
    and nothing the Portal or the SDK adds. ``system`` is the system prompt.

    What cannot be sent raises ``InvalidRequestError`` before a request is
    made: no image or more than ``MAX_IMAGES``, an empty image, one over
    ``MAX_IMAGE_BYTES``, more than ``MAX_TOTAL_BYTES`` together, bytes that
    are not JPEG, PNG, WebP or GIF, a media type that is none of those or
    not the one of the bytes, a prompt that is blank or not a string.

    ``reasoning_effort`` is as in ``efp_app.llm``: ``"low"``, ``"medium"`` or
    ``"high"``, ``None`` for ``low``, and a ``ValueError`` for anything else.
    The call is sent once and never repeated.
    """
    body = _body(images, prompt, system, max_output_tokens, reasoning_effort)
    http = client or _client.get_client()
    return _inspection(
        http.post(_PATH, body, visitor=visitor, timeout=INSPECT_TIMEOUT if timeout is None else timeout)
    )


async def ainspect(
    images: Images,
    prompt: str,
    *,
    system: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> Inspection:
    """:func:`inspect` for asynchronous code."""
    body = _body(images, prompt, system, max_output_tokens, reasoning_effort)
    http = client or _client.get_async_client()
    return _inspection(
        await http.post(_PATH, body, visitor=visitor, timeout=INSPECT_TIMEOUT if timeout is None else timeout)
    )
