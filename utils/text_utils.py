import re
from bs4 import BeautifulSoup
import hashlib

def normalize_text(string: str) -> str:
    if not string:
        return ""
    
    string = BeautifulSoup(string, "html.parser").get_text(separator=' ')
    string = re.sub(r"\s+", ' ', string).strip().lower()
    return string

def hash_text(string: str) -> str:
    text_hash = hashlib.new("sha256")
    text_hash.update(string.encode("utf-8"))
    return text_hash.hexdigest()