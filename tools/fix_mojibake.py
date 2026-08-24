import re
from pathlib import Path

def fix_text_mojibake(text: str) -> str:
    # Fix common UTF-8 -> CP1252 / Latin-1 double encoding artifacts in Vietnamese text
    replacements = {
        "bÃ£i biá»ƒn": "bãi biển",
        "Má»¹ KhÃª": "Mỹ Khê",
        "ÄÃ  Náºµng": "Đà Nẵng",
        "sÃ¡ng sá»›m": "sáng sớm",
        "hoáº·c": "hoặc",
        "tá»± nhiên": "tự nhiên",
        "tá»± nhiÃªn": "tự nhiên",
        "khÃ´ng gian": "không gian",
        "khÃ´ng pháº£i": "không phải",
        "phÃ²ng gym": "phòng gym",
        "Cáº§u Rá»“ng": "Cầu Rồng",
        "sÃ´ng HÃ n": "sông Hàn",
        "buá»•i tá»‘i": "buổi tối",
        "Ã¡nh Ä‘Ã¨n": "ánh đèn",
        "thÃ nh phá»‘": "thành phố",
        "tá»±a nháº¹": "tựa nhẹ",
        "lan can": "lan can",
        "bá» tÆ°á»ng": "bờ tường",
        "gÃ³c ba pháº§n tÆ°": "góc ba phần tư",
        "cÃ´ng viÃªn": "công viên",
        "vá»›i": "với",
        "phÃa sau": "phía sau",
        "BÃ¬nh Tháº¡nh": "Bình Thạnh",
        "ngá»“i trÃªn": "ngồi trên",
        "gháº¿": "ghế",
        "tÆ° tháº¿": "tư thế",
        "thÆ° giÃ£n": "thư giãn",
        "bÆ°á»›c": "bước",
        "cháºm": "chậm",
        "trÃªn phá»‘": "trên phố",
        "nhÃ¬n sang": "nhìn sang",
        "mÃ¡y quay": "máy quay",
        "Báº¿n Ninh Kiá»u": "Bến Ninh Kiều",
        "Cáº§n ThÆ¡": "Cần Thơ",
        "chiá»u tá»‘i": "chiều tối",
        "sÃ´ng Háºu": "sông Hậu",
        "cáº§m ly": "cầm ly",
        "cÃ  phÃª": "cà phê",
        "mang Ä‘i": "mang đi",
        "chá»‰nh tÃ³c": "chỉnh tóc",
        "má»™t láº§n": "một lần",
        "rá»“i": "rồi",
        "má»‰m cÆ°á»i": "mỉm cười",
        "nháº¹": "nhẹ",
        "chÆ°a cÃ³": "chưa có",
        "khÃ´ng tá»“n táº¡i": "không tồn tại",
        "táº£i": "tải",
        "tháº¥t báº¡i": "thất bại",
        "thÃ nh cÃ´ng": "thành công",
    }
    
    # Try general UTF-8 bytes recovery if string contains mojibake sequences
    res = text
    for bad, good in replacements.items():
        res = res.replace(bad, good)
        
    return res

def clean_file(path: Path):
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8", errors="ignore")
    cleaned = fix_text_mojibake(content)
    if cleaned != content:
        path.write_text(cleaned, encoding="utf-8")
        print(f"Fixed mojibake in {path}")

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    clean_file(root / "modules" / "flow_content" / "app.py")
    clean_file(root / "modules" / "parenting" / "app.py")
