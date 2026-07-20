import re
from bs4 import BeautifulSoup


def normalize_text(string: str) -> str:
    if not string:
        return ""
    
    string = BeautifulSoup(string, "html.parser").get_text(separator=' ')
    string = re.sub(r"\s+", ' ', string).strip().lower()
    return string