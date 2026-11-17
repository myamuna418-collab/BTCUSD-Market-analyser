"""Field-level screenshot OCR; no historical price substitution."""
import io, re, math
from collections import Counter
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract

FIELDS = ("open", "high", "low", "close")
PATTERNS = {
 "open": re.compile(r"(?<![A-Za-z0-9])[O0©]\s*[:=]?\s*(\d{2,3}[,.]\d{3}[.,]\d)", re.I),
 "high": re.compile(r"(?<![A-Za-z0-9])H\s*[:=]?\s*(\d{2,3}[,.]\d{3}[.,]\d)", re.I),
 "low": re.compile(r"(?<![A-Za-z0-9])L\s*[:=]?\s*(\d{2,3}[,.]\d{3}[.,]\d)", re.I),
 "close": re.compile(r"(?<![A-Za-z0-9])C\s*[:=]?\s*(\d{2,3}[,.]\d{3}[.,]\d)", re.I),
}
def extract_fields(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as opened:
        im = ImageOps.exif_transpose(opened).convert("RGB")
    w,h=im.size
    if w<500 or h<250:
        return {"ohlc": {}, "uncertain": list(FIELDS), "votes": {}, "readings": []}
    # Relative chart-header crops. All numbers come exclusively from screenshot pixels.
    region=im.crop((int(w*.11),int(h*.051),int(w*.54),int(h*.096)))
    variants=[region.resize((region.width*3,region.height*3)),
              ImageEnhance.Contrast(region.resize((region.width*4,region.height*4))).enhance(1.8)]
    variants.append(ImageOps.autocontrast(ImageOps.grayscale(variants[0])))
    readings=[]
    for variant in variants:
        for psm in (6,7):
            readings.append(pytesseract.image_to_string(variant,config=f"--psm {psm}").strip())
    votes={k:[] for k in FIELDS}
    for reading in readings:
        for key,pat in PATTERNS.items():
            match=pat.search(reading)
            if not match: continue
            raw=match.group(1)
            if "," not in raw and raw.count(".") == 1: continue
            try:
                value=float(raw.replace(",",""))
                if math.isfinite(value) and 1000<value<1000000:
                    votes[key].append(value)
            except ValueError: pass
    # One candidate alone is not adequate evidence; require independent OCR passes.
    result={}
    for key, values in votes.items():
        if not values: continue
        ranked=Counter(values).most_common()
        if ranked[0][1]>=2 and (len(ranked)==1 or ranked[0][1]>ranked[1][1]):
            result[key]=ranked[0][0]
    if len(result)==4:
        o,hi,lo,c=(result[k] for k in FIELDS)
        if not lo<=min(o,c)<=max(o,c)<=hi:
            # Contradiction means some OCR digits are wrong; require user confirmation.
            result={}
    return {"ohlc":result,"uncertain":[k for k in FIELDS if k not in result],
            "votes":{k:dict(Counter(v)) for k,v in votes.items()},
            "readings":readings}
