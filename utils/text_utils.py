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

def build_question_key(question_text: str, answer_texts: list[str] | None = None) -> str:
    """Builds the identity key for a question: normalized text plus sorted normalized answers."""
    text = normalize_text(question_text)
    answers = sorted(
        normalize_text(a) for a in (answer_texts or []) if a and a.strip()
    )
    return text + (' ' + ' '.join(answers) if answers else '')