"""The Google half of the digests: auth, and the four API calls step 13 makes.

**This is the only module in Referat that opens a socket to anything but a model
download**, and it runs only when somebody types `referat project link-doc` or
`project sync`. What it sends is `notes.md` and nothing else -- never
`transcript.md`, never a WAV, never `.voices/`, never an embedding. That
Convention is restated in this file because this is the file somebody would
break it in: the doc is right there, and "the doc should really have the exact
quote" is one line of code away.

**Every `google` import is inside a function body**, so this module is
importable in the base install and `referat/cli.py` can name its functions at
module scope. The same arrangement `referat/rerun.py` uses to keep the three
gigabytes of the `transcribe` extra out of every other command;
:func:`available` is the guard the three verbs call before they touch anything.

**Auth binds no socket, deliberately.** The usual desktop flow is
`InstalledAppFlow.run_local_server`, which starts an HTTP server on loopback to
catch the redirect -- and `CLAUDE.md`'s Conventions forbid exactly that: *any
localhost server ... anything binding a port*, with the test stated as whether
something binds a socket. Google's out-of-band flow was shut off in 2022, so the
remaining option is the paste flow below: build the consent URL, open it, and
read back the URL the browser fails to load. Ten lines instead of one, and no
exception to a load-bearing rule.

It has a second property worth keeping. It needs a terminal, so **the command
center can never authenticate** -- a window that popped a browser consent from
a GUI thread would be a network round trip on the thread that owns the recorder.
Phase 7's failure mode is a sentence saying to run one command from a prompt.
"""

from __future__ import annotations

import logging
import re
import sys
import webbrowser
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from referat import paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from referat.config import Config

log = logging.getLogger(__name__)

SCOPES = (
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
)
"""The two scopes, and the second one is the one worth arguing about.

`documents` is unavoidable: there is no narrower Docs scope that writes, and
`documents.create` puts a new doc in My Drive without any Drive *write* scope,
so none is asked for.

`drive.metadata.readonly` is what makes *select existing doc* work, and
**`drive.file` is not sufficient** -- it grants access only to files this
application itself created or that were handed to it through Google's own file
picker, so on a fresh install a search would return an empty list and the
picker would be dead on arrival. `drive.readonly` would also work and would
additionally read every byte of every file in the Drive; this one returns names,
ids and modification times and no content at all, which is all a picker needs.
"""

REDIRECT_URI = "http://localhost"
"""What the consent redirects to, and nothing listens there on purpose.

Registered on the Desktop client. The browser lands on a connection error and
its address bar holds the `code`, which is what gets pasted back. See the module
docstring for why nothing is listening.
"""

DOC_URL = "https://docs.google.com/document/d/{}/edit"
MEETINGS_TAB = "Meetings"

DOC_ID_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")
BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def parse_doc_ref(text: str) -> tuple[str, str]:
    """A share link or a bare id, as `(gdoc_id, tab_id)`. `("", "")` if it is neither.

    **Nobody has a document id to hand; everybody has the link.** It is what the
    Share button copies and what the address bar holds, so requiring the
    forty-four characters out of the middle of it was asking a person to do a
    parser's job.

    The tab comes free with it. A Docs URL carries `?tab=t.xxx` whenever the
    document has more than one and you are looking at one of them — so copying
    the link while standing on the tab you want says which tab you want, and the
    caller never has to ask. That is worth more here than anywhere else, because
    the alternative is naming a tab whose title you have to spell exactly.

    Accepts a bare id too, since `--doc` took one before this existed and a
    document id is unambiguous at that length.
    """
    text = text.strip()
    if not text:
        return "", ""
    if (match := DOC_ID_RE.search(text)) is not None:
        parsed = urlparse(text)
        tab = (parse_qs(parsed.query).get("tab") or [""])[0]
        # A fragment can carry it too: .../edit#tab=t.0
        if not tab and parsed.fragment.startswith("tab="):
            tab = parsed.fragment[4:]
        return match.group(1), tab.strip()
    if BARE_ID_RE.match(text):
        return text, ""
    return "", ""


def tab_titles(document: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Every tab in the document as `(title, tab_id, depth)`, in document order.

    What a refusal lists. Naming the tab you meant is only reasonable if
    something tells you what the tabs are called, and a document with ten of
    them — which is a real one here — is exactly where guessing fails.
    """
    out: list[tuple[str, str, int]] = []

    def walk(tabs: list[dict[str, Any]], depth: int) -> None:
        for tab in tabs or []:
            properties = tab.get("tabProperties") or {}
            out.append((properties.get("title", ""), properties.get("tabId", ""), depth))
            walk(tab.get("childTabs") or [], depth + 1)

    walk(document.get("tabs") or [], 0)
    return out


class GoogleError(Exception):
    """Anything that went wrong out there, said in one sentence a person can act on.

    Wraps `HttpError` rather than letting it out, because its `repr` is a JSON
    blob and the three verbs report through `cli.Outcome`, whose message is the
    words somebody reads.
    """


def available() -> str:
    """`""` when the `digest` extra is installed, or the sentence saying it is not.

    Asked **first**, before `projects.json` is opened, because a complaint about
    an unknown project id is advice about the wrong problem when the thing that
    would do the work is not installed at all.
    """
    try:
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
    except ImportError:
        return (
            "the digests need the `digest` extra, which is not installed. "
            "Install it with: pip install google-api-python-client google-auth-oauthlib"
        )
    return ""


# --- Auth -------------------------------------------------------------------


def credentials(config: Config) -> Any:
    """The stored credentials, refreshed, or a consent asked for at the terminal.

    Three states, in order: a cached token that is still valid; one that has
    expired but carries a refresh token, which is refreshed silently; and
    nothing usable, which asks. Only the third needs a person, and on this
    machine it should happen approximately once -- provided the OAuth app's
    publishing status is *In production* rather than *Testing*, because Google
    expires a testing app's refresh tokens after seven days and the consent
    would come back weekly.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = config.google_token_file()
    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), list(SCOPES))
        except (OSError, ValueError):
            # The same shape as every other unreadable state file here: log it,
            # carry on as though there were none. The cost is one more consent.
            log.warning("cannot read %s; asking for consent again", token_path, exc_info=True)
            creds = None

    if creds is not None and creds.valid:
        return creds
    reason = ""
    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - any refresh failure means ask again
            log.warning("refreshing the Google token failed; asking for consent", exc_info=True)
            reason = _refresh_reason(exc)
        else:
            _store(creds, token_path)
            return creds

    return _consent(config, token_path, reason)


def _refresh_reason(exc: Exception) -> str:
    """Why the stored token stopped working, said in terms of what to change.

    **`invalid_grant` almost always means one thing here**, and it is worth
    naming rather than leaving somebody to conclude that this asks every time.
    Google expires the refresh tokens of an OAuth app whose publishing status is
    still *Testing* after seven days -- so a setup that worked on Monday asks
    again the following Tuesday, and again the Tuesday after that, and looks
    exactly like a bug in Referat. It is one setting in the console.

    The other causes are named too, because the fix for each is somewhere else
    entirely and a wrong guess wastes an afternoon.
    """
    detail = str(exc)
    if "invalid_grant" not in detail:
        return f"the stored token stopped working ({detail})."
    return (
        "the stored token was rejected. The usual cause is that the OAuth app is still "
        "in TESTING in the Google Cloud console, where Google expires refresh tokens "
        "after seven days -- set its publishing status to 'In production' and this stops "
        "happening. Otherwise: access was revoked at myaccount.google.com, the OAuth "
        "client was deleted, or this machine's clock is wrong."
    )


def _consent(config: Config, token_path: Any, reason: str = "") -> Any:
    """The paste flow. Binds nothing, and needs a terminal by design.

    `reason` is why a stored token is not being used, so a *second* consent
    explains itself. Being asked twice with no explanation is how somebody
    concludes they are going to be asked forever.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = config.google_client_secret_file()
    if not secret.exists():
        raise GoogleError(
            f"no Google client secret at {secret}. Create an OAuth client ID of type "
            "'Desktop app' in the Google Cloud console, download the JSON, and save it "
            "there -- SETUP.md section 13 has the walkthrough."
        )
    if not sys.stdin or not sys.stdin.isatty():
        raise GoogleError(
            "Google consent has to be given once at a terminal. Run "
            "`referat project link-doc <project-id>` from a prompt, and every later "
            "sync -- from the command center included -- will use the stored token."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(secret), list(SCOPES), redirect_uri=REDIRECT_URI
    )
    # `offline` is what mints a refresh token at all, and `consent` is what makes
    # Google issue a new one rather than reusing a grant that has none attached.
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    print("\nReferat needs your permission to write its digests into Google Docs.")
    if reason:
        print(f"\nYou have consented before, and {reason}")
    print("\nOpening your browser. If it does not open, paste this in yourself:\n")
    print(f"  {url}\n")
    print("Approve it. The browser will then fail to load a page at localhost --")
    print("that is expected, nothing is listening there. Copy the whole address")
    print("out of the address bar and paste it below.\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box is not a failure
        log.debug("could not open a browser", exc_info=True)

    try:
        pasted = input("Address (or just the code): ").strip()
    except EOFError:
        # `isatty()` says there is a terminal and reading it says there is not,
        # which is what a piped or captured stdin looks like. Nothing was saved.
        raise GoogleError(
            "there is no terminal to read the authorization code from. Run "
            "`referat project link-doc <project-id>` directly at a prompt -- once. "
            "Every later sync uses the token it stores."
        ) from None
    code = _code_from(pasted)
    if not code:
        raise GoogleError("no authorization code in what was pasted; nothing was saved")
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001
        raise GoogleError(f"Google refused the authorization code: {exc}") from exc

    _store(flow.credentials, token_path)
    print(f"\nSaved to {token_path}. Delete that file to sign out.\n")
    return flow.credentials


def _code_from(pasted: str) -> str:
    """The `code` out of a pasted redirect URL, or the code itself if that is what came.

    Both, because somebody who has been told to copy an address will sometimes
    copy the interesting part of it instead, and refusing that would be a
    refusal about punctuation.
    """
    if pasted.startswith("http://") or pasted.startswith("https://"):
        return (parse_qs(urlparse(pasted).query).get("code") or [""])[0]
    return pasted


def _store(creds: Any, token_path: Any) -> None:
    """Cache the refresh token, atomically and creating the folder if it is new.

    No permission bits are set: this is Windows, where a file in the user profile
    is already the user's, and `os.chmod` cannot express a Unix mode here
    anyway. The Conventions rule about no `if sys.platform` branches cuts the
    same way -- there is nothing to branch on.
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)
    paths.write_text_atomic(token_path, creds.to_json())


# --- Services ---------------------------------------------------------------


def _service(config: Config, name: str, version: str) -> Any:
    from googleapiclient.discovery import build

    return build(name, version, credentials=credentials(config), cache_discovery=False)


def docs(config: Config) -> Any:
    return _service(config, "docs", "v1")


def drive(config: Config) -> Any:
    return _service(config, "drive", "v3")


# --- Reading ----------------------------------------------------------------


def get_document(config: Config, gdoc_id: str) -> dict[str, Any]:
    """One document, **always with `includeTabsContent=True`**.

    Never without. The response of a plain `documents.get` carries only the first
    tab's `body` and no `tabs` at all, so every later `tabId` would be looked up
    in something that does not describe the document -- and the whole tab
    discipline this step runs on would quietly evaporate.
    """
    try:
        return (
            docs(config)
            .documents()
            .get(documentId=gdoc_id, includeTabsContent=True)
            .execute()
        )
    except GoogleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GoogleError(_explain(exc, f"reading document {gdoc_id}")) from exc


def find_tab(
    document: dict[str, Any], *, title: str = "", tab_id: str = ""
) -> dict[str, Any] | None:
    """One tab by title or by id, searched through the whole tree.

    **Recursive, and that is not a nicety.** A document's `tabs` is a tree: every
    tab carries `childTabs`. A `Meetings` tab nested one level under another
    would be invisible to a flat scan, and the consequence is not an error but a
    *second* `Meetings` tab created beside the one that was already there.
    """
    def walk(tabs: list[dict[str, Any]]) -> dict[str, Any] | None:
        for tab in tabs or []:
            properties = tab.get("tabProperties") or {}
            if tab_id and properties.get("tabId") == tab_id:
                return tab
            if title and (properties.get("title") or "").strip() == title:
                return tab
            found = walk(tab.get("childTabs") or [])
            if found is not None:
                return found
        return None

    return walk(document.get("tabs") or [])


def tab_content(tab: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """One tab's structural elements, and the index a block may be appended at.

    `body_end` is the last element's `endIndex` **minus one**, never the
    `endIndex` itself: a segment's final newline cannot be deleted and nothing
    can be inserted after it, so an operation aimed there is rejected. Getting
    this wrong shows up only on the last block in the tab, which is exactly the
    one every append touches.
    """
    content = ((tab.get("documentTab") or {}).get("body") or {}).get("content") or []
    end = max((int(e.get("endIndex", 1)) for e in content), default=2)
    return content, end - 1


def read_tab(config: Config, gdoc_id: str, tab_id: str) -> tuple[list[dict[str, Any]], int, str]:
    """The content, `body_end` and current *title* of one stored tab.

    **Found by id and never by title**, which is what makes renaming a tab safe:
    a Docs `tabId` is minted once and does not move, so a project stays linked to
    the tab it was linked to whatever somebody later calls it. Only *deleting*
    the tab breaks the link, and that is what the refusal below is about.

    The title comes back so a caller can correct the `tab_name` it has stored,
    which is display text and would otherwise still say what the tab was called
    on the day it was linked.
    """
    document = get_document(config, gdoc_id)
    tab = find_tab(document, tab_id=tab_id)
    if tab is None:
        raise GoogleError(
            f"the tab this project is linked to is no longer in {gdoc_id} -- it was "
            f"deleted, or the document was replaced. Renaming it would have been fine, "
            f"since the link is by tab id. Unlink and link it again."
        )
    content, end = tab_content(tab)
    return content, end, (tab.get("tabProperties") or {}).get("title", "")


def search_docs(config: Config, query: str, limit: int = 20) -> list[dict[str, str]]:
    """Google Docs in this Drive whose name contains `query`, most recent first."""
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    q = (
        "mimeType='application/vnd.google-apps.document' "
        f"and name contains '{escaped}' and trashed=false"
    )
    try:
        result = (
            drive(config)
            .files()
            .list(
                q=q,
                orderBy="modifiedTime desc",
                pageSize=limit,
                fields="files(id,name,modifiedTime,owners(displayName))",
            )
            .execute()
        )
    except GoogleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GoogleError(_explain(exc, "searching Drive")) from exc
    return [
        {
            "gdoc_id": f.get("id", ""),
            "name": f.get("name", ""),
            "modified": (f.get("modifiedTime") or "")[:10],
            "owner": ((f.get("owners") or [{}])[0]).get("displayName", ""),
        }
        for f in result.get("files", [])
    ]


# --- Writing ----------------------------------------------------------------


def create_doc(config: Config, title: str) -> tuple[str, str, str]:
    """A new document, and the id and name of the tab a digest goes into.

    The tab id is **read back** out of a `documents.get` rather than assumed: a
    new document has exactly one tab, but its id is Google's to choose, and every
    later write is located by it.
    """
    try:
        created = docs(config).documents().create(body={"title": title}).execute()
    except GoogleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GoogleError(_explain(exc, f"creating {title!r}")) from exc

    gdoc_id = created.get("documentId", "")
    document = get_document(config, gdoc_id)
    tabs = document.get("tabs") or []
    if not tabs:
        raise GoogleError(f"created {gdoc_id} but it reports no tabs, which should not happen")
    properties = tabs[0].get("tabProperties") or {}
    return gdoc_id, properties.get("tabId", ""), properties.get("title", "")


def apply(config: Config, gdoc_id: str, requests: list[dict[str, Any]]) -> None:
    """One `batchUpdate`. Atomic: it either all lands or none of it does.

    That atomicity is the recovery story for a sync. A block whose batch fails
    leaves the document exactly as it was and its `meta.json` unwritten, so the
    next sync sees the same work still to do and does it. There is no state in
    between to clean up, which is why a failure here stops that document and
    reports it rather than trying to carry on against indices that may have
    moved.
    """
    if not requests:
        return
    _require_tab_ids(requests)
    try:
        docs(config).documents().batchUpdate(
            documentId=gdoc_id, body={"requests": requests}
        ).execute()
    except GoogleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GoogleError(_explain(exc, f"writing to document {gdoc_id}")) from exc


def _require_tab_ids(requests: list[dict[str, Any]]) -> None:
    """Refuse a batch in which anything is not located by a tab.

    **A request carrying no `tabId` silently targets the first tab** -- writing a
    meeting into somebody's unrelated notes, with no error to notice and nothing
    in a log to find later. So it is checked here, mechanically, at the one place
    every write passes through, rather than left to each call site to remember.
    A rule belongs where somebody would break it.
    """
    for request in requests:
        for name, body in request.items():
            located = False
            for key in ("range", "location", "endOfSegmentLocation"):
                target = body.get(key)
                if isinstance(target, dict):
                    located = True
                    if not target.get("tabId"):
                        raise GoogleError(
                            f"a {name} request carries no tabId, which would silently "
                            f"write into the document's first tab. Refusing the batch."
                        )
            if not located:
                raise GoogleError(f"a {name} request names no location at all; refusing the batch")


def doc_url(gdoc_id: str) -> str:
    return DOC_URL.format(gdoc_id)


def _explain(exc: Exception, doing: str) -> str:
    """An `HttpError` as one sentence. Its own `repr` is a JSON blob.

    Callers re-raise a :class:`GoogleError` unchanged rather than passing it
    through here. Wrapping one produced "Google refused while searching Drive:
    EOF when reading a line" for a consent that could not be asked for -- a
    sentence that sends somebody to look at the wrong machine entirely.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    detail = getattr(exc, "reason", None) or str(exc)
    if status == 403:
        detail += " (check that the Docs and Drive APIs are enabled for this project)"
    elif status == 404:
        detail += " (the document may have been deleted, or is not shared with this account)"
    return f"Google refused while {doing}: {detail}"
