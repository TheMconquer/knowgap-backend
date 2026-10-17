import re
from bs4 import BeautifulSoup
import hashlib

def hash_text(string: str) -> str:
    """This function hashes an input string and returns the hash."""
    text_hash = hashlib.new("sha256")
    text_hash.update(string.encode("utf-8"))
    return text_hash.hexdigest()

def parse_html(html: str) -> tuple[str, list[str], list[str]]:
    """Parses HTML once and returns its normalized text plus any embedded Canvas image
    file IDs and URLs."""
    if not html:
        return "", [], []
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", ' ', soup.get_text(separator=' ')).strip().lower()
    ids, urls = [], []
    for img in soup.find_all("img"):
        endpoint = img.get("data-api-endpoint", "")
        src = img.get("src", "")
        if "/files/" in endpoint:
            ids.append(endpoint.split("/files/")[-1].split("/")[0])
            if src:
                urls.append(src)
    return text, ids, urls

def normalize_text(string: str) -> str:
    """This function strips HTML elements from an input and then formats the text minimally."""
    return parse_html(string)[0]

def build_question_key(question_text: str, answer_texts: list[str] | None = None,
                       fallback_id: str | int | None = None) -> str:
    """Builds the identity key for a question: normalized text, any embedded image file IDs,
    and sorted normalized answers.

    If that comes out empty (no text, no Canvas image, no answers), falls back to
    question_<fallback_id> so empty questions don't all share the key "". Callers must pass
    the Canvas question id so question fetches and submission syncs produce the same key."""
    text, ids, _ = parse_html(question_text)
    if ids:
        text = (text + " attachment_" + "_".join(ids)).strip()
    answers = sorted(
        normalize_text(a) for a in (answer_texts or []) if a and a.strip()
    )
    key = (text + ' ' + ' '.join(answers)).strip()
    if not key and fallback_id is not None:
        return f"question_{fallback_id}"
    return key