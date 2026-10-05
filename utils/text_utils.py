import re
from bs4 import BeautifulSoup
import hashlib

def normalize_text(string: str) -> str:
    """This function strips HTML elements from an input and then formats the text minimally."""
    if not string:
        return ""
    
    string = BeautifulSoup(string, "html.parser").get_text(separator=' ')
    string = re.sub(r"\s+", ' ', string).strip().lower()
    return string

def hash_text(string: str) -> str:
    """This function hashes an input string and returns the hash."""
    text_hash = hashlib.new("sha256")
    text_hash.update(string.encode("utf-8"))
    return text_hash.hexdigest()

def build_question_hash(raw_key: str) -> str:
    normalized = normalize_text(raw_key)
    return hash_text(normalized)

def extract_attachments(html: str) -> tuple[list[str], list[str]]:
    """Canvas file IDs and image URLs embedded in question HTML."""
    ids, urls = [], []
    if not html:
        return ids, urls
    for img in BeautifulSoup(html, "html.parser").find_all("img"):
        endpoint = img.get("data-api-endpoint", "")
        src = img.get("src", "")
        if "/files/" in endpoint:
            ids.append(endpoint.split("/files/")[-1].split("/")[0])
            if src:
                urls.append(src)
    return ids, urls

def build_question_key(question_text: str, answer_texts: list[str] | None = None,
                       fallback_id: str | int | None = None) -> str:
    """Builds the identity key for a question: normalized text, any embedded image file IDs,
    and sorted normalized answers.

    If that comes out empty (no text, no Canvas image, no answers), falls back to
    question_<fallback_id> so empty questions don't all share the key "". Callers must pass
    the Canvas question id so question fetches and submission syncs produce the same key."""
    text = normalize_text(question_text)
    ids, _ = extract_attachments(question_text)
    if ids:
        text = (text + " attachment_" + "_".join(ids)).strip()
    answers = sorted(
        normalize_text(a) for a in (answer_texts or []) if a and a.strip()
    )
    key = (text + ' ' + ' '.join(answers)).strip()
    if not key and fallback_id is not None:
        return f"question_{fallback_id}"
    return key