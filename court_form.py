#!/usr/bin/env python3
"""สร้างเอกสารยื่นศาลตามแบบพิมพ์ศาลยุติธรรมเป็น PDF จากไฟล์ข้อมูล JSON

ใช้:  python3 court_form.py spec.json out.pdf [--html out.html]
      python3 court_form.py --list            (แสดงรายชื่อแบบที่รองรับ)

หลักการ
- วาดแบบพิมพ์ (หัวแบบ ตราครุฑ ช่องกรอก เส้นบรรทัด) ด้วย HTML แล้วพิมพ์เป็น PDF ด้วย Chromium
- ตัดคำไทยด้วยพจนานุกรม ICU และจัดชิดขอบแบบไทยโดยกระจายช่องไฟตามรอยต่อคำ
- เนื้อความยาวไหลต่อหน้า ๒ และใบต่อ (๔๐ ก.) อัตโนมัติ ทุกบรรทัดลงเส้นบรรทัดพอดี
- คู่ความหลายคน: หัวแบบเขียน "… กับพวกรวม … คน" และทำใบต่อรายละเอียดชื่อและที่อยู่
- ช่องใดข้อความยาวเกินช่อง สคริปต์แจ้งชื่อช่องและคืนค่า 2 ห้ามส่งไฟล์นั้นให้ผู้ใช้จนกว่าจะแก้
"""
import json, sys, os, html, re, base64, argparse, subprocess, tarfile, glob, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
# ที่เก็บไฟล์ประกอบ: ข้าง ๆ สคริปต์ (assets/) หรือโครงเดิมของ skill (../assets/court-forms)
ASSETS = next((p for p in (os.path.join(HERE, "assets"), os.path.join(HERE, "..", "assets", "court-forms"))
               if os.path.isdir(p)), os.path.join(HERE, "assets"))
CACHE = os.environ.get("COURT_FORM_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "court-form")
FONT_FAMILY = "'CourtThai'"
# ใช้ TH SarabunPSK ถ้ามีไฟล์ใน assets/fonts (THSarabun*.ttf) มิฉะนั้นใช้ TH Sarabun New (รูปอักษรชุดเดียวกัน)
FONT_SETS = [
    (("THSarabun.ttf", 400, "normal"), ("THSarabun Bold.ttf", 700, "normal"),
     ("THSarabun Italic.ttf", 400, "italic"), ("THSarabun BoldItalic.ttf", 700, "italic")),
    (("THSarabunNew.ttf", 400, "normal"), ("THSarabunNew-Bold.ttf", 700, "normal"),
     ("THSarabunNew-Italic.ttf", 400, "italic"), ("THSarabunNew-BoldItalic.ttf", 700, "italic")),
]
# ฟอนต์ฉบับเดียวกับที่ศาลใช้ (SIPA) เผยแพร่เป็นแพ็กเกจ npm — ไฟล์ตรงกับชุดในปลั๊กอินทุกไบต์
FONT_NPM = "@apw-pdf/fonts"
FONT_NPM_MAP = {"THSarabunNew-Regular.ttf": "THSarabunNew.ttf"}


def font_dir():
    """คืนโฟลเดอร์ฟอนต์ที่ใช้ได้จริง — ข้าง ๆ สคริปต์ · แคชในเครื่อง · มิฉะนั้นดึงจาก npm มาเก็บแคช"""
    local = os.path.join(ASSETS, "fonts")
    for d in (local, os.path.join(CACHE, "fonts")):
        if any(os.path.exists(os.path.join(d, fs[0][0])) for fs in FONT_SETS):
            return d
    return fetch_fonts(os.path.join(CACHE, "fonts"))


def fetch_fonts(dest):
    """ดึง TH Sarabun จาก npm (รีจิสทรีเป็นปลายทางที่สภาพแวดล้อมของ Claude เปิดให้เสมอ)"""
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(CACHE, "_npm")
    os.makedirs(tmp, exist_ok=True)
    try:
        subprocess.run(["npm", "pack", FONT_NPM], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
    except FileNotFoundError:
        raise SystemExit("ไม่พบคำสั่ง npm และไม่มีไฟล์ฟอนต์ในเครื่อง — วางไฟล์ THSarabunNew*.ttf ไว้ที่ "
                         + os.path.join(ASSETS, "fonts"))
    except subprocess.CalledProcessError as e:
        raise SystemExit("ดึงฟอนต์จาก npm ไม่สำเร็จ: " + (e.stderr or b"").decode()[-300:])
    tgz = max(glob.glob(os.path.join(tmp, "*.tgz")), key=os.path.getmtime)
    with tarfile.open(tgz) as tf:
        for m in tf.getmembers():
            base = os.path.basename(m.name)
            if m.isfile() and base.lower().endswith(".ttf"):
                src = tf.extractfile(m)
                if src:
                    with open(os.path.join(dest, FONT_NPM_MAP.get(base, base)), "wb") as f:
                        shutil.copyfileobj(src, f)
    shutil.rmtree(tmp, ignore_errors=True)
    if not any(os.path.exists(os.path.join(dest, fs[0][0])) for fs in FONT_SETS):
        raise SystemExit(f"แพ็กเกจ {FONT_NPM} ไม่มีไฟล์ฟอนต์ที่ต้องใช้")
    return dest

PT_W, PT_H = 595.28, 841.89
LM, RM = 85.0, 553.0          # ขอบซ้าย 3 ซม. / ปลายเส้นขวา (ขอบขวา 1.5 ซม.)
BOTTOM = 797.0                # ขอบล่างของพื้นที่บรรทัด
ROW = 25                      # ระยะแถวหัวแบบ

TH_DIG = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
TH_WORD = ["ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า", "สิบ",
           "สิบเอ็ด", "สิบสอง", "สิบสาม", "สิบสี่", "สิบห้า", "สิบหก", "สิบเจ็ด", "สิบแปด", "สิบเก้า", "ยี่สิบ"]

FORMS = {
    "complaint": "(๔) คำฟ้อง + (๕) คำขอท้ายคำฟ้องแพ่ง หรือ (๖) คำขอท้ายคำฟ้องอาญา + หลังแบบผู้เรียง/ผู้พิมพ์",
    "request": "(๗) คำร้อง / คำแถลง / คำขอ (เลือก kind)",
    "answer": "(๑๑) คำให้การจำเลย (ฟ้องแย้งเขียนในเนื้อคำให้การ)",
    "appeal": "(๓๒) อุทธรณ์ + (๓๓) ท้ายอุทธรณ์",
    "appeal_reply": "(๓๔) คำแก้อุทธรณ์ + (๓๕) ท้ายคำแก้อุทธรณ์",
    "dika": "(๓๖) ฎีกา + (๓๗) ท้ายฎีกา",
    "dika_reply": "(๓๘) คำแก้ฎีกา + (๓๙) ท้ายคำแก้ฎีกา",
    "proxy": "(๑๐) ใบมอบฉันทะ",
    "witness_list": "(๑๕) บัญชีพยาน + (๑๕ ทวิ) ใบต่อบัญชีพยาน",
    "lawyer_appointment": "ใบแต่งทนายความ (แบบใหม่ E-Filing)",
    "property_list": "(๘) บัญชีทรัพย์",
    "witness_statement": "(๒๑ ทวิ) บันทึกถ้อยคำแทนการซักถามพยาน",
    "settlement": "(๒๙) สัญญาประนีประนอมยอมความ",
    "bail_request": "(๕๗) คำร้องขอปล่อยชั่วคราว + หน้าคำสั่งศาล",
    "bail_contract": "(๕๘) สัญญาประกัน + หน้าคำสั่งศาล",
}


def th(n):
    return str(n).translate(TH_DIG)


def th_word(n):
    return TH_WORD[n] if 0 <= n < len(TH_WORD) else th(n)


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def yy(y):
    """ปี พ.ศ. ในช่องหลัง ๒๕ — รับ '๒๕๖๙' หรือ '๖๙'"""
    return re.sub(r"^(๒๕|25)(?=\S\S$)", "", str(y or ""))


# ---------------------------------------------------------------- การตัดบรรทัด
D = r"[๐-๙0-9]"
MONTHS = "มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม"
UNITS = ("บาท|สตางค์|ปี|เดือน|วัน|ตัน|คน|ฉบับ|หลัง|ชั่วโมง|นาที|ไร่|งาน|ตารางวา|ตารางเมตร|เมตร|กิโลกรัม|"
         "ครั้ง|แปลง|เล่ม|หน้า|รายการ|ชุด|คัน|เครื่อง|ห้อง|หุ้น|ราย|อันดับ|ปาก")
# คำประสมที่ห้ามตัดกลางคำ (ICU ตัดตามคำในพจนานุกรม จึงอาจแยกคำประสมกฎหมาย) — เพิ่มได้ใน assets/keep_words.txt
KEEP_WORDS = """
ค้ำประกัน ผู้ค้ำประกัน บอกกล่าว ทวงถาม ลูกหนี้ร่วม ค่าฤชาธรรมเนียม ค่าทนายความ ค่าเสียหาย ค่าสินไหมทดแทน
สินไหมทดแทน ดอกเบี้ย ผิดนัด ผิดสัญญา นิติบุคคล กรรมสิทธิ์ ครอบครองปรปักษ์ ภาระจำยอม เจ้าพนักงาน
พนักงานสอบสวน พนักงานอัยการ เจ้าพนักงานบังคับคดี ห้างหุ้นส่วนจำกัด ห้างหุ้นส่วนสามัญ บริษัทจำกัด
ประมวลกฎหมาย พระราชบัญญัติ หนังสือรับรอง หนังสือมอบอำนาจ ศาลชั้นต้น ศาลอุทธรณ์ ศาลฎีกา คำพิพากษา
ทุนทรัพย์ เบี้ยปรับ มัดจำ เช่าซื้อ ขายฝาก ซื้อขาย จำนอง จำนำ กู้ยืม ผู้จัดการมรดก พินัยกรรม
ทายาทโดยธรรม สินสมรส ค่าอุปการะเลี้ยงดู ค่าชดเชย สินจ้างแทนการบอกกล่าวล่วงหน้า เลิกจ้าง
ประทับตรา ลายมือชื่อ ผู้มีอำนาจกระทำการแทน ตามกฎหมาย หมิ่นประมาท ฉ้อโกง ยักยอก ขายทอดตลาด
ผู้เรียงและพิมพ์ ผู้เขียนหรือพิมพ์ ควรมิควรแล้วแต่จะโปรด ทนายความโจทก์ ทนายความจำเลย ใบเสร็จรับเงิน ใบแต่งทนายความ ใบมอบฉันทะ บัญชีระบุพยาน ลงวันที่ เอกสารท้าย ฟ้องแย้ง ประเด็นข้อพิพาท อายุความ ขาดอายุความ ฟ้องซ้อน ฟ้องซ้ำ ดำเนินกระบวนพิจารณา
""".split()
PROTECT = [
    re.compile(rf"(?:โจทก์|จำเลย|ผู้ร้อง|ผู้คัดค้าน|ผู้ร้องสอด|ผู้ถูกกล่าวหา)?ที่\s?{D}+(?:\s?(?:และ|ถึง)\s?(?:ที่\s?)?{D}+)?"),
    re.compile(rf"(?:ข้อ|มาตรา|หมายเลข|ครั้งที่|ฉบับที่|เลขที่|หมู่ที่|ลำดับที่|อนุมาตรา|วรรค|โฉนดที่ดินเลขที่|"
               rf"คดีหมายเลข(?:ดำ|แดง)ที่)\s?{D}[๐-๙0-9/.,()\-]*"),
    re.compile(rf"(?:(?:ลง)?วันที่\s?)?{D}{{1,2}}\s(?:{MONTHS})\s(?:พ\.ศ\.\s?)?{D}{{4}}"),
    re.compile(rf"{D}[๐-๙0-9,\.]*\s?(?:{UNITS})"),
    re.compile(r"\([^()\s]{1,12}\)"),
]
NAMES = []          # ชื่อคู่ความจาก spec (ห้ามตัดกลางชื่อ)


def _keep_words():
    extra = os.path.join(ASSETS, "keep_words.txt")
    w = list(KEEP_WORDS)
    if os.path.exists(extra):
        for ln in open(extra, encoding="utf-8"):
            if not ln.lstrip().startswith("#"):
                w += ln.split()
    return sorted(set(w), key=len, reverse=True)


KEEP_RE = None


def protect_ranges(s):
    global KEEP_RE
    if KEEP_RE is None:
        KEEP_RE = re.compile("|".join(map(re.escape, _keep_words())))
    rng = []
    pats = PROTECT + [KEEP_RE]
    if NAMES:
        pats.append(re.compile("|".join(map(re.escape, sorted(NAMES, key=len, reverse=True)))))
    for pat in pats:
        rng += [(m.start(), m.end()) for m in pat.finditer(s) if m.end() - m.start() > 1]
    rng.sort()
    merged = []
    for a, b in rng:
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    for a, b in reversed(merged):
        s = s[:a] + "\x01" + s[a:b] + "\x02" + s[b:]
    return s


def rich(s):
    """มาร์กอัปในเนื้อความ: **หนา**  __ขีดเส้นใต้__  ~~ขีดฆ่า~~  [[ห้ามตัดบรรทัดในวงเล็บคู่]]
    คุ้มกันอัตโนมัติไม่ให้ตัดบรรทัดกลาง: ชื่อคู่ความ ที่ ๑ / ข้อ ๑ / มาตรา ๑ / วันที่เต็ม / จำนวน+หน่วย / คำประสมกฎหมาย"""
    s = esc(protect_ranges("" if s is None else str(s)))
    s = s.replace("\x01", '<span class="nb">').replace("\x02", "</span>")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<u>\1</u>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"\[\[(.+?)\]\]", r'<span class="nb">\1</span>', s)
    return s


def data_uri(path, mime):
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def choose(options, chosen):
    """ข้อความเลือก เช่น คำร้อง / คำแถลง / คำขอ — ขีดฆ่าตัวที่ไม่ใช้"""
    return " / ".join(esc(o) if o == chosen else f"<s>{esc(o)}</s>" for o in options)


# ---------------------------------------------------------------- ชิ้นส่วนแบบ
class Page:
    def __init__(self, kind="p"):
        self.kind, self.parts, self.flow = kind, [], None   # flow = (fid, top, bottom, opts)

    def add(self, h):
        self.parts.append(h)
        return self

    def html(self):
        return f'<section class="page {self.kind}">' + "".join(self.parts) + "</section>"


def row(top, segs, left=LM, right=RM, cls=""):
    """แถวหัวแบบ: segs = ('t', ป้าย) | ('h', html) | ('f', ค่า, กว้าง pt หรือ None=ยืด, ชื่อช่อง) | ('id', เลข) | ('sp', pt)"""
    out = []
    for sg in segs:
        k = sg[0]
        if k == "t":
            out.append(f'<span class="lb">{esc(sg[1])}</span>')
        elif k == "h":
            out.append(f'<span class="lb">{sg[1]}</span>')
        elif k == "sp":
            out.append(f'<span style="flex:0 0 {sg[1]}pt"></span>')
        elif k == "f":
            val, w = sg[1], sg[2]
            fname = sg[3] if len(sg) > 3 else ""
            style = f"flex:0 0 {w}pt" if w else "flex:1 1 0"
            c = "f c" if (w and w <= 40) else "f"
            out.append(f'<span class="{c}" style="{style}" data-name="{esc(fname)}"><span class="v">{esc(val)}</span></span>')
        elif k == "id":
            out.append(id_boxes(sg[1]))
    return f'<div class="row {cls}" style="top:{top}pt;left:{left}pt;width:{right-left}pt">' + "".join(out) + "</div>"


def id_boxes(num):
    d = re.sub(r"\D", "", str(num or "").translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")))
    d = (d + " " * 13)[:13]
    out, i = ['<span class="ids">'], 0
    for gi, g in enumerate([1, 4, 5, 2, 1]):
        for _ in range(g):
            out.append(f'<span class="box">{esc(d[i].strip())}</span>')
            i += 1
        if gi < 4:
            out.append('<span class="dash">-</span>')
    out.append("</span>")
    return "".join(out)


def text_at(top, left, s, extra=""):
    return f'<div class="tx" style="top:{top}pt;left:{left}pt;{extra}">{s}</div>'


def circle(top, left):
    return f'<div class="circ" style="top:{top}pt;left:{left}pt"></div>'


def hline(top, left=LM, right=RM, style="solid"):
    return f'<div class="hl" style="top:{top}pt;left:{left}pt;width:{right-left}pt;border-top-style:{style}"></div>'


def sigline(top, left, right, label, name=None, prefix="ลงชื่อ"):
    h = (text_at(top - 17, left - 34, esc(prefix)) if prefix else "")
    h += f'<div class="sig" style="top:{top}pt;left:{left}pt;width:{right-left}pt"></div>' + text_at(top - 17, right + 3, esc(label))
    if name:
        h += f'<div class="tx" style="top:{top + 4}pt;left:{left}pt;width:{right-left}pt;text-align:center">({esc(name)})</div>'
    return h


def window(fid, top, start, cap, used, P, left=LM, right=RM, rule="solid", cols=None):
    rl = "".join(f'<div class="rl" style="top:{(i + 1) * P - 1}pt;border-top-style:{rule}"></div>' for i in range(cap)) \
        if rule else ""
    if cols:
        x = 0
        for w in cols[:-1]:
            x += w
            rl += f'<div class="vl" style="left:{x}pt"></div>'
    return (f'<div class="win" style="top:{top}pt;left:{left}pt;width:{right-left}pt;height:{cap*P}pt">{rl}'
            f'<div class="clip" style="height:{used*P}pt"><div class="flowcopy" data-flow="{fid}" '
            f'style="top:{-start*P}pt"></div></div></div>')


# ---------------------------------------------------------------- คู่ความ
def P_(x):
    """คู่ความรับได้ทั้ง dict และสตริงชื่อ"""
    return {"name": x} if isinstance(x, str) else dict(x)


def bkk(p):
    return "กรุงเทพ" in (p.get("province") or "")


JURISTIC_PREFIX = ("บริษัท", "ห้างหุ้นส่วน", "ธนาคาร", "มูลนิธิ", "สมาคม", "นิติบุคคล", "สหกรณ์", "กรม", "กระทรวง",
                   "สำนักงาน", "องค์การ", "การไฟฟ้า", "การประปา", "มหาวิทยาลัย", "เทศบาล", "องค์การบริหารส่วน")


def is_juristic(p):
    if "juristic" in p:
        return bool(p["juristic"])
    return (p.get("name") or "").startswith(JURISTIC_PREFIX)


def nos_text(nos):
    """[1, 3] → 'ที่ ๑ และที่ ๓' · [1, 2, 3] → 'ที่ ๑ ที่ ๒ และที่ ๓'"""
    t = [f"ที่ {th(n)}" for n in nos]
    return t[0] if len(t) == 1 else " ".join(t[:-1]) + " และ" + t[-1]


NO_DEFAULT = ("ไม่มีข้อมูลทนายหรือผู้เรียงค่าตั้งต้น (ผู้ใช้กำหนด 25 ต.ค. 2569) — "
              "ถามผู้ใช้ในงานนั้นแล้วใส่ข้อมูลลง spec")


def with_default(x):
    """คู่ความ/ผู้ยื่น/ผู้มอบ — ต้องระบุข้อมูลใน spec ทุกครั้ง ไม่มีค่าตั้งต้น"""
    if x == "default" or (isinstance(x, dict) and x.get("base") == "default"):
        raise SystemExit(NO_DEFAULT)
    return P_(x)


def side_name(lst):
    n = lst[0]["name"]
    return n if len(lst) == 1 else f"{n} กับพวกรวม {th(len(lst))} คน"


def address_html(p):
    b = bkk(p)
    bits = []
    if p.get("house_no"):
        bits.append(("สำนักงานใหญ่ตั้งอยู่เลขที่" if is_juristic(p) else "อยู่บ้านเลขที่") + f" {p['house_no']}")
    if p.get("moo"): bits.append(f"หมู่ที่ {p['moo']}")
    if p.get("soi"): bits.append(f"ซอย{p['soi']}")
    if p.get("road"): bits.append(f"ถนน{p['road']}")
    if p.get("subdistrict"): bits.append(("แขวง" if b else "ตำบล") + p["subdistrict"])
    if p.get("district"): bits.append(("เขต" if b else "อำเภอ") + p["district"])
    if p.get("province"): bits.append(p["province"] if b else "จังหวัด" + p["province"])
    if p.get("postcode"): bits.append(p["postcode"])
    return " ".join(f'<span class="nb">{esc(x)}</span>' for x in bits)


def party_list_flow(parties, role, nos=None):
    """ใบต่อ รายละเอียดชื่อและที่อยู่ของ… (แต่ละคนเป็นก้อนเดียว ไม่แยกข้ามหน้า) · nos = เลขลำดับจริงของคู่ความ"""
    h = [f'<div class="ctr">รายละเอียดชื่อและที่อยู่ของ{esc(role)}</div>']
    nos = nos or list(range(1, len(parties) + 1))
    for i, p in zip(nos, parties):
        jur = is_juristic(p)
        b = [f'<div class="pl"><span class="pk">{esc(role)}ที่ {th(i)}</span>{rich(p.get("name",""))}</div>']
        if p.get("id"):
            d = re.sub(r"\D", "", p["id"])
            grp = f"{d[0]} {d[1:5]} {d[5:10]} {d[10:12]} {d[12:]}" if len(d) == 13 else p["id"]
            b.append(f'<div class="pd">{"ทะเบียนนิติบุคคลเลขที่" if jur else "เลขประจำตัวประชาชน"} {esc(grp)}</div>')
        if not jur:
            rc = [f"เชื้อชาติ {p['race']}"] if p.get("race") else []
            rc += [f"สัญชาติ {p['nationality']}"] if p.get("nationality") else []
            if rc: b.append(f'<div class="pd">{esc("   ".join(rc))}</div>')
            oc = [f"อาชีพ {p['occupation']}"] if p.get("occupation") else []
            oc += [f"อายุ {p['age']} ปี"] if p.get("age") else []
            if oc: b.append(f'<div class="pd">{esc("   ".join(oc))}</div>')
        if p.get("extra"): b.append(f'<div class="pd">{rich(p["extra"])}</div>')
        al = address_html(p)
        if al: b.append(f'<div class="pd">{al}</div>')
        ct = [f"โทรศัพท์ {p['phone']}"] if p.get("phone") else []
        ct += [f"ไปรษณีย์อิเล็กทรอนิกส์ {p['email']}"] if p.get("email") else []
        if ct: b.append(f'<div class="pd">{esc("   ".join(ct))}</div>')
        h.append('<div class="keep">' + "".join(b) + "</div>")
    return "".join(h)


def body_flow(items):
    """เนื้อความ: สตริง = ย่อหน้า (ย่อหน้าแรก 2.54 ซม.)
    {"text":..., "align":"center|right|none"}   {"sign": "ผู้ร้อง", "name": "ชื่อ", "prefix": "ลงชื่อ"}   {"blank": 1}"""
    out = []
    for p in items:
        if isinstance(p, str):
            p = {"text": p}
        if "keep" in p:
            out.append('<div class="keep">' + body_flow(p["keep"]) + "</div>")
        elif "sign" in p:
            nm = f'<div class="sgn">({esc(p["name"])})</div>' if p.get("name") else ""
            out.append(f'<div class="keep"><div class="blank"></div><div class="sg"><span>{esc(p.get("prefix","ลงชื่อ"))}</span>'
                       f'<span class="sgl"></span><span>{esc(p["sign"])}</span></div>{nm}</div>')
        elif "blank" in p:
            out.append('<div class="blank"></div>' * int(p["blank"]))
        else:
            cls = {"center": "ctr", "right": "rgt", "none": "noind"}.get(p.get("align", ""), "par")
            out.append(f'<div class="{cls}">{rich(p["text"])}</div>')
    return "".join(out)


# ---------------------------------------------------------------- หัวแบบร่วม
def header(pg, S, no, title_html, red=True, title2=None, title3=None, mark=True, court_use=False):
    d = S.get("date", {})
    if court_use:
        pg.add(text_at(30, 455, "<u>สำหรับศาลใช้</u>"))
    pg.add((circle(45, 72) if mark else "") + text_at(38, 100 if mark else 72, esc(no))
           + text_at(60, 100 if mark else 72, title_html))
    if title2:
        pg.add(text_at(82, 100 if mark else 72, title2))
    if title3:
        pg.add(text_at(104, 100 if mark else 72, title3))
    pg.add(f'<img class="garuda" src="{GARUDA}" style="top:32pt;left:250pt;width:60pt">')
    pg.add(row(86 if red else 96, [("t", "คดีหมายเลขดำที่"), ("f", S.get("black_no", ""), 70, "คดีหมายเลขดำ"),
                                   ("t", "/๒๕"), ("f", yy(S.get("black_year", "")), None, "ปีคดีดำ")], left=360))
    if red:
        pg.add(row(110, [("t", "คดีหมายเลขแดงที่"), ("f", S.get("red_no", ""), 70, "คดีหมายเลขแดง"),
                         ("t", "/๒๕"), ("f", yy(S.get("red_year", "")), None, "ปีคดีแดง")], left=353))
    pg.add(row(148, [("t", "ศาล"), ("f", S.get("court", ""), None, "ศาล")], left=303))
    pg.add(row(173, [("t", "วันที่"), ("f", d.get("day", ""), 30, "วันที่"), ("t", "เดือน"),
                     ("f", d.get("month", ""), None, "เดือน"), ("t", "พุทธศักราช ๒๕"),
                     ("f", yy(d.get("year", "")), 26, "ปี")], left=268))
    pg.add(row(198, [("t", "ความ"), ("f", S.get("case_kind", ""), None, "ความ")], left=303))
    pl = [P_(x) for x in S.get("plaintiffs", [])] or [{"name": ""}]
    df = [P_(x) for x in S.get("defendants", [])] or [{"name": ""}]
    lab = S.get("side_labels", ["โจทก์", "จำเลย"])
    pg.add(text_at(288, LM, "ระหว่าง", "font-weight:bold;font-size:18pt"))
    pg.add('<svg class="brace" style="top:262pt;left:134pt" width="20pt" height="84pt" viewBox="0 0 20 92" '
           'preserveAspectRatio="none"><path d="M17 1 C8 1 9 6 9 14 L9 38 C9 44 6 46 2 46 C6 46 9 48 9 54 L9 78 '
           'C9 86 8 91 17 91" fill="none" stroke="#000" stroke-width="1"/></svg>')
    pg.add(row(268, [("f", side_name(pl), None, "ชื่อฝ่ายโจทก์ในวงเล็บ"), ("sp", 8), ("t", lab[0])], left=158))
    pg.add(row(318, [("f", side_name(df), None, "ชื่อฝ่ายจำเลยในวงเล็บ"), ("sp", 8), ("t", lab[1])], left=158))
    return 352


def person_rows(pg, t, p, pre, style="complaint", role=""):
    """แถวรายละเอียดบุคคลตามแบบ
    complaint: ID+role+เชื้อชาติ / สัญชาติ อาชีพ อายุ บ้านเลขที่ หมู่ / ถนน ซอย ตำบล อำเภอ / จังหวัด รหัส โทร / โทรสาร อีเมล
    request:   ID / เชื้อชาติ สัญชาติ อาชีพ เกิดวันที่ / อายุ บ้าน หมู่ ถนน / ซอย ตำบล อำเภอ / จังหวัด รหัส โทร / โทรสาร อีเมล
    answer:    ID+เชื้อชาติ / สัญชาติ อาชีพ เกิด อายุ / บ้าน หมู่ ถนน ซอย / ตำบล อำเภอ จังหวัด / รหัส โทร โทรสาร / อีเมล"""
    g = lambda k: p.get(k, "")
    f = lambda k, w, n: ("f", g(k), w, f"{pre}.{n}")
    R = lambda segs, **kw: pg.add(row(t, segs, **kw))
    if style == "complaint":
        R([("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", g("id")), ("sp", 6), ("t", role), ("t", " เชื้อชาติ"),
           f("race", None, "เชื้อชาติ")]); t += ROW
        R([("t", "สัญชาติ"), f("nationality", 48, "สัญชาติ"), ("t", "อาชีพ"), f("occupation", 62, "อาชีพ"),
           ("t", "อายุ"), f("age", 28, "อายุ"), ("t", "ปี อยู่บ้านเลขที่"), f("house_no", None, "บ้านเลขที่"),
           ("t", "หมู่ที่"), f("moo", 32, "หมู่ที่")]); t += ROW
        R([("t", "ถนน"), f("road", 76, "ถนน"), ("t", "ตรอก/ซอย"), f("soi", 76, "ซอย"), ("t", "ตำบล/แขวง"),
           f("subdistrict", 64, "ตำบล"), ("t", "อำเภอ/เขต"), f("district", None, "อำเภอ")]); t += ROW
        R([("t", "จังหวัด"), f("province", 110, "จังหวัด"), ("t", "รหัสไปรษณีย์"), f("postcode", 80, "รหัสไปรษณีย์"),
           ("t", "โทรศัพท์"), f("phone", None, "โทรศัพท์")]); t += ROW
        R([("t", "โทรสาร"), f("fax", 120, "โทรสาร"), ("t", "ไปรษณีย์อิเล็กทรอนิกส์"), f("email", None, "อีเมล")]); t += ROW
    elif style == "request":
        R([("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", g("id"))]); t += ROW
        R([("t", "เชื้อชาติ"), f("race", 45, "เชื้อชาติ"), ("t", "สัญชาติ"), f("nationality", 45, "สัญชาติ"),
           ("t", "อาชีพ"), f("occupation", None, "อาชีพ"), ("t", "เกิดวันที่"), f("birth_day", 26, "เกิดวันที่"),
           ("t", "เดือน"), f("birth_month", 62, "เกิดเดือน"), ("t", "พ.ศ."), f("birth_year", 40, "เกิดปี")]); t += ROW
        R([("t", "อายุ"), f("age", 28, "อายุ"), ("t", "ปี อยู่บ้านเลขที่"), f("house_no", 80, "บ้านเลขที่"),
           ("t", "หมู่ที่"), f("moo", 32, "หมู่ที่"), ("t", "ถนน"), f("road", None, "ถนน")]); t += ROW
        R([("t", "ตรอก/ซอย"), f("soi", 110, "ซอย"), ("t", "ตำบล/แขวง"), f("subdistrict", 100, "ตำบล"),
           ("t", "อำเภอ/เขต"), f("district", None, "อำเภอ")]); t += ROW
        R([("t", "จังหวัด"), f("province", 110, "จังหวัด"), ("t", "รหัสไปรษณีย์"), f("postcode", 80, "รหัสไปรษณีย์"),
           ("t", "โทรศัพท์"), f("phone", None, "โทรศัพท์")]); t += ROW
        R([("t", "โทรสาร"), f("fax", 120, "โทรสาร"), ("t", "ไปรษณีย์อิเล็กทรอนิกส์"), f("email", None, "อีเมล")]); t += ROW
    elif style == "answer":
        R([("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", g("id")), ("sp", 6), ("t", "เชื้อชาติ"),
           f("race", None, "เชื้อชาติ")]); t += ROW
        R([("t", "สัญชาติ"), f("nationality", 45, "สัญชาติ"), ("t", "อาชีพ"), f("occupation", None, "อาชีพ"),
           ("t", "เกิดวันที่"), f("birth_day", 26, "เกิดวันที่"), ("t", "เดือน"), f("birth_month", 62, "เกิดเดือน"),
           ("t", "พ.ศ."), f("birth_year", 36, "เกิดปี"), ("t", "อายุ"), f("age", 26, "อายุ"), ("t", "ปี")]); t += ROW
        R([("t", "อยู่บ้านเลขที่"), f("house_no", 80, "บ้านเลขที่"), ("t", "หมู่ที่"), f("moo", 32, "หมู่ที่"),
           ("t", "ถนน"), f("road", 100, "ถนน"), ("t", "ตรอก/ซอย"), f("soi", None, "ซอย")]); t += ROW
        R([("t", "ตำบล/แขวง"), f("subdistrict", 100, "ตำบล"), ("t", "อำเภอ/เขต"), f("district", 100, "อำเภอ"),
           ("t", "จังหวัด"), f("province", None, "จังหวัด")]); t += ROW
        R([("t", "รหัสไปรษณีย์"), f("postcode", 70, "รหัสไปรษณีย์"), ("t", "โทรศัพท์"), f("phone", 120, "โทรศัพท์"),
           ("t", "โทรสาร"), f("fax", None, "โทรสาร")]); t += ROW
        R([("t", "ไปรษณีย์อิเล็กทรอนิกส์"), f("email", None, "อีเมล")]); t += ROW
    elif style == "address":
        R([("t", "อยู่บ้านเลขที่"), f("house_no", 80, "บ้านเลขที่"), ("t", "หมู่ที่"), f("moo", 32, "หมู่ที่"),
           ("t", "ถนน"), f("road", 100, "ถนน"), ("t", "ตรอก/ซอย"), f("soi", None, "ซอย")]); t += ROW
        R([("t", "ตำบล/แขวง"), f("subdistrict", 100, "ตำบล"), ("t", "อำเภอ/เขต"), f("district", 100, "อำเภอ"),
           ("t", "จังหวัด"), f("province", None, "จังหวัด")]); t += ROW
        R([("t", "รหัสไปรษณีย์"), f("postcode", 70, "รหัสไปรษณีย์"), ("t", "โทรศัพท์"), f("phone", 120, "โทรศัพท์"),
           ("t", "โทรสาร"), f("fax", None, "โทรสาร")]); t += ROW
        R([("t", "ไปรษณีย์อิเล็กทรอนิกส์"), f("email", None, "อีเมล")]); t += ROW
    return t


def drafter_block(pg, t, doc, dr, ty, with_id=True):
    """ผู้เรียง / ผู้เขียนหรือพิมพ์ (หลังแบบคำฟ้อง และแบบท้ายอุทธรณ์ ท้ายฎีกา)"""
    g = lambda d, k: d.get(k, "")
    pg.add(row(t, [("t", f"{doc}ฉบับนี้ข้าพเจ้า"), ("f", g(dr, "name"), None, "ผู้เรียง")], left=102)); t += ROW
    if with_id:
        pg.add(row(t, [("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", g(dr, "id"))])); t += ROW
    pg.add(row(t, [("t", "ทนายความใบอนุญาตที่"), ("f", g(dr, "license"), 80, "ใบอนุญาต"),
                   ("t", "อยู่บ้านเลขที่"), ("f", g(dr, "house_no"), 60, "ผู้เรียง.บ้านเลขที่"),
                   ("t", "หมู่ที่"), ("f", g(dr, "moo"), 30, "ผู้เรียง.หมู่"), ("t", "ถนน"),
                   ("f", g(dr, "road"), None, "ผู้เรียง.ถนน")])); t += ROW
    pg.add(row(t, [("t", "ตรอก/ซอย"), ("f", g(dr, "soi"), 170, "ผู้เรียง.ซอย"), ("t", "ตำบล/แขวง"),
                   ("f", g(dr, "subdistrict"), None, "ผู้เรียง.ตำบล")])); t += ROW
    pg.add(row(t, [("t", "อำเภอ/เขต"), ("f", g(dr, "district"), 95, "ผู้เรียง.อำเภอ"), ("t", "จังหวัด"),
                   ("f", g(dr, "province"), 115, "ผู้เรียง.จังหวัด"), ("t", "รหัสไปรษณีย์"),
                   ("f", g(dr, "postcode"), None, "ผู้เรียง.รหัส")])); t += ROW
    pg.add(row(t, [("t", "โทรศัพท์"), ("f", g(dr, "phone"), 190, "ผู้เรียง.โทรศัพท์"), ("t", "โทรสาร"),
                   ("f", g(dr, "fax"), None, "ผู้เรียง.โทรสาร")])); t += ROW
    pg.add(row(t, [("t", "ไปรษณีย์อิเล็กทรอนิกส์"), ("f", g(dr, "email"), 230, "ผู้เรียง.อีเมล"),
                   ("t", " เป็นผู้เรียง")])); t += ROW
    pg.add(sigline(t + 55, 330, 470, "ผู้เรียง", prefix=None)); t += 110
    pg.add(row(t, [("t", f"{doc}ฉบับนี้ข้าพเจ้า"), ("f", g(ty, "name"), None, "ผู้พิมพ์")], left=102)); t += ROW
    pg.add(row(t, [("t", "อยู่บ้านเลขที่"), ("f", g(ty, "house_no"), 70, "ผู้พิมพ์.บ้านเลขที่"), ("t", "หมู่ที่"),
                   ("f", g(ty, "moo"), 40, "ผู้พิมพ์.หมู่"), ("t", "ถนน"), ("f", g(ty, "road"), None, "ผู้พิมพ์.ถนน")])); t += ROW
    pg.add(row(t, [("t", "ตรอก/ซอย"), ("f", g(ty, "soi"), 170, "ผู้พิมพ์.ซอย"), ("t", "ตำบล/แขวง"),
                   ("f", g(ty, "subdistrict"), None, "ผู้พิมพ์.ตำบล")])); t += ROW
    pg.add(row(t, [("t", "อำเภอ/เขต"), ("f", g(ty, "district"), 95, "ผู้พิมพ์.อำเภอ"), ("t", "จังหวัด"),
                   ("f", g(ty, "province"), 115, "ผู้พิมพ์.จังหวัด"), ("t", "รหัสไปรษณีย์"),
                   ("f", g(ty, "postcode"), None, "ผู้พิมพ์.รหัส")])); t += ROW
    pg.add(row(t, [("t", "โทรศัพท์"), ("f", g(ty, "phone"), 190, "ผู้พิมพ์.โทรศัพท์"),
                   ("t", " เป็นผู้เขียนหรือพิมพ์")])); t += ROW
    pg.add(sigline(t + 55, 330, 470, "ผู้เขียนหรือพิมพ์", prefix=None))
    return t + 80


def lawyer(S, key):
    """ผู้เรียง/ผู้พิมพ์/ทนาย จาก spec เท่านั้น (ผู้พิมพ์ไม่ระบุ = ผู้เรียง) · ไม่มีข้อมูล = เว้นช่องว่างให้เขียนเอง"""
    v = S.get(key)
    if v is None and key == "typist":
        v = S.get("drafter")
    if v == "default":
        raise SystemExit(NO_DEFAULT)
    return P_(v) if v else {}


def std_closing(S, doc_word, signer_label, signer_name):
    """ท้ายเนื้อความของคำร้อง/คำให้การ: ควรมิควร… ลงชื่อ… และผู้เรียงและพิมพ์"""
    if S.get("closing") is False:
        return []
    dr = lawyer(S, "drafter")
    out = [{"keep": [{"text": "ควรมิควรแล้วแต่จะโปรด", "align": "center"},
                     {"sign": signer_label, "name": signer_name}]}]
    if dr.get("name"):
        role = S.get("drafter_role", dr.get("role", ""))
        out.append({"keep": [
            {"text": f"{doc_word}ฉบับนี้ ข้าพเจ้า [[{dr['name']}]] [[{role}]] เป็นผู้เรียงและพิมพ์".replace("[[]]", "").replace("  ", " ")},
            {"sign": "ผู้เรียงและพิมพ์", "name": dr["name"]}]})
    return out


# ---------------------------------------------------------------- แบบแต่ละชนิด
def form_complaint(S, flows, seq):
    pl = [P_(x) for x in S["plaintiffs"]]
    df = [P_(x) for x in S["defendants"]]
    pg = Page("first")
    t = header(pg, S, "(๔)", "คำฟ้อง", red=False)
    pg.add(row(t, [("t", "ข้อหาหรือฐานความผิด"), ("f", S.get("charge", ""), None, "ข้อหา")])); t += ROW
    pg.add(row(t, [("t", "จำนวนทุนทรัพย์"), ("f", S.get("claim_baht", ""), None, "ทุนทรัพย์"), ("t", "บาท"),
                   ("f", S.get("claim_satang", ""), 48, "สตางค์"), ("t", "สตางค์")])); t += ROW
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", pl[0]["name"] + (" โจทก์ที่ ๑" if len(pl) > 1 else ""), None, "ข้าพเจ้า")],
               left=168)); t += ROW
    t = person_rows(pg, t, pl[0], "โจทก์", "complaint", "โจทก์")
    pg.add(row(t, [("t", "ขอยื่นฟ้อง"), ("f", df[0]["name"] + (" จำเลยที่ ๑" if len(df) > 1 else ""), None, "ขอยื่นฟ้อง")]))
    t += ROW
    t = person_rows(pg, t, df[0], "จำเลย", "complaint", "จำเลย")
    pg.add(text_at(t + 3, LM, "มีข้อความตามที่จะกล่าวต่อไปนี้"))
    flows["body"] = body_flow(S["body"])
    pg.flow = ("body", t + 25, BOTTOM)
    seq += [pg, ("cont", "body", "p2")]
    for role, key in (("โจทก์", "plaintiffs"), ("จำเลย", "defendants")):
        lst = [P_(x) for x in S[key]]
        if len(lst) > 1:
            flows["list_" + key] = party_list_flow(lst, role)
            seq.append(("cont", "list_" + key, "list"))
    pr = S["prayer"]
    crim = pr.get("type") == "criminal"
    p5 = Page("prayer")
    p5.add(circle(74, 92) + text_at(68, 121, "(๖)" if crim else "(๕)")
           + text_at(90, 121, "คำขอท้ายคำฟ้องอาญา" if crim else "คำขอท้ายคำฟ้องแพ่ง"))
    if crim:
        intro = [f'<div class="par">การที่จำเลยได้กระทำตามข้อความที่กล่าวมาในคำฟ้องนั้น ข้าพเจ้าถือว่าเป็นความผิดต่อกฎหมาย '
                 f'และบทมาตราดังนี้ คือ <u>{rich(pr.get("sections", ""))}</u></div>',
                 f'<div class="par">ขอให้ศาลออกหมาย <u>{esc(pr.get("warrant", "เรียก"))}</u> จำเลยมาพิจารณาพิพากษาลงโทษ'
                 f'ตามกฎหมาย และขอให้ศาลสั่งและบังคับจำเลยตามคำขอต่อไปนี้</div>']
    else:
        intro = ['<div class="par">เพราะฉะนั้น ขอศาลออกหมายเรียกตัวจำเลยมาพิจารณาพิพากษา และบังคับจำเลยตามคำขอต่อไปนี้</div>']
    S["_docx_body"] = (list(S["body"]) + [{"text": "คำขอท้ายคำฟ้อง", "align": "center"}]
                       + [{"text": x} for x in pr["items"]]
                       + [{"sign": pr.get("signer_label", "โจทก์"), "name": pr.get("signer_name")}])
    flows["prayer"] = "".join(intro) + "".join(f'<div class="par">{rich(x)}</div>' for x in pr["items"])
    p5.flow = ("prayer", 128, 520, {"strict": "คำขอท้ายฟ้องยาวเกินช่องในแบบ — ย่อคำขอ หรือย้ายรายละเอียดไปไว้ในเนื้อคำฟ้อง"})
    p5.add(f'<div class="tx" style="top:536pt;left:{LM}pt;width:{RM-LM}pt;text-indent:72pt">'
           f'ข้าพเจ้าได้ยื่นสำเนาคำฟ้อง โดยข้อความถูกต้องเป็นอย่างเดียวกันมาด้วย</div>')
    copies = pr.get("copies") or th_word(len(df))
    p5.add(row(560, [("f", copies, 60, "จำนวนสำเนา"), ("t", " ฉบับ และรอฟังคำสั่งอยู่ หากไม่รอถือว่าทราบแล้ว")],
               right=LM + 330))
    p5.add(sigline(650, 360, 505, pr.get("signer_label", "โจทก์"), pr.get("signer_name")))
    p5.add(text_at(745, 515, "(พลิก)"))
    seq.append(p5)
    bk = Page("back")
    drafter_block(bk, 75, "คำฟ้อง", lawyer(S, "drafter"), lawyer(S, "typist"))
    seq.append(bk)


def first_page_footer(pg, note_signer_html, extra=None, top=706, dashed=False):
    pg.add(hline(top, style="dashed" if dashed else "solid"))
    pg.add(text_at(top + 6, LM, '<u>หมายเหตุ</u>&nbsp;&nbsp;&nbsp;ข้าพเจ้ารอฟังคำสั่งอยู่ ถ้าไม่รอให้ถือว่าทราบแล้ว'))
    pg.add(f'<div class="sig" style="top:{top + 52}pt;left:330pt;width:140pt"></div>'
           + text_at(top + 35, 473, note_signer_html))
    if extra:
        pg.add(text_at(764, LM, extra, "font-size:14pt"))


def form_request(S, flows, seq):
    kinds = ["คำร้อง", "คำแถลง", "คำขอ"]
    kind = S.get("kind", "คำร้อง")
    who = {"คำร้อง": "ผู้ร้อง", "คำแถลง": "ผู้แถลง", "คำขอ": "ผู้ขอ"}
    f = with_default(S["filer"])
    pg = Page("first")
    t = header(pg, S, "(๗)", choose(kinds, kind))
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", (f["name"] + " " + f.get("role_text", "")).strip(), None, "ข้าพเจ้า")],
               left=168)); t += ROW
    t = person_rows(pg, t, f, "ผู้ยื่น", "request")
    pg.add(text_at(t + 3, LM, f"ขอยื่น{choose(kinds, kind)} มีข้อความตามที่จะกล่าวต่อไปนี้"))
    first_page_footer(pg, choose([who[k] for k in kinds], who[kind]))
    body = list(S["body"]) + std_closing(S, kind, S.get("signer_label", who[kind]), S.get("signer_name", f["name"]))
    S["_docx_body"] = body
    flows["body"] = body_flow(body)
    pg.flow = ("body", t + 25, 700)
    seq += [pg, ("cont", "body", "p2")]


def form_answer(S, flows, seq):
    ans = [P_(x) for x in S.get("answering", S["defendants"])]
    nos = [int(str(n).translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))) for n in S.get("answering_nos", [])]
    if nos and len(nos) != len(ans):
        raise SystemExit("answering_nos ต้องมีจำนวนเท่ากับ answering")
    n_def = len(S.get("defendants", [])) or len(ans)
    pg = Page("first")
    t = header(pg, S, "(๑๑)", "คำให้การจำเลย")
    if len(ans) == 1:
        nm = ans[0]["name"] + (f" ({nos_text(nos)})" if nos and n_def > 1 else "")
    else:
        nm = f"{ans[0]['name']} กับพวกรวม {th(len(ans))} คน" + (f" ({nos_text(nos)})" if nos else "")
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", nm, None, "ข้าพเจ้า"), ("t", "จำเลย")], left=168)); t += ROW
    t = person_rows(pg, t, ans[0], "จำเลย", "answer")
    pg.add(text_at(t + 3, LM, "ได้ทราบคำฟ้องตลอดแล้ว ขอให้การตามที่จะกล่าวต่อไปนี้"))
    first_page_footer(pg, "จำเลย", "(กรณีจำเลยหลายคนให้ระบุชื่อและรายละเอียดของจำเลยแต่ละคนแนบท้ายคำให้การจำเลย)")
    signer = S.get("signer_label", "จำเลย")
    dflt = ans[0]["name"] if len(ans) == 1 and not is_juristic(ans[0]) else None
    body = list(S["body"]) + std_closing(S, "คำให้การ", signer, S.get("signer_name", dflt))
    S["_docx_body"] = body
    flows["body"] = body_flow(body)
    pg.flow = ("body", t + 25, 700)
    seq += [pg, ("cont", "body", "p2")]
    if len(ans) > 1:
        flows["list_ans"] = party_list_flow(ans, "จำเลย", nos or None)
        seq.append(("cont", "list_ans", "list"))


APPEAL = {
    "appeal": dict(no="(๓๒)", title="อุทธรณ์", tail_no="(๓๓)", tail="ท้ายอุทธรณ์", doc="อุทธรณ์", signer="ผู้อุทธรณ์"),
    "dika": dict(no="(๓๖)", title="ฎีกา", tail_no="(๓๗)", tail="ท้ายฎีกา", doc="ฎีกา", signer="ผู้ฎีกา"),
    "appeal_reply": dict(no="(๓๔)", title="คำแก้อุทธรณ์", tail_no="(๓๕)", tail="ท้ายคำแก้อุทธรณ์", doc="คำแก้อุทธรณ์",
                         signer="ผู้แก้อุทธรณ์"),
    "dika_reply": dict(no="(๓๘)", title="คำแก้ฎีกา", tail_no="(๓๙)", tail="ท้ายคำแก้ฎีกา", doc="คำแก้ฎีกา",
                       signer="ผู้แก้ฎีกา"),
}


def form_appeal(S, flows, seq, kind):
    c = APPEAL[kind]
    pg = Page("first")
    t = header(pg, S, c["no"], c["title"])
    if kind in ("appeal", "dika"):
        pg.add(row(t, [("t", "ข้อหาหรือฐานความผิด"), ("f", S.get("charge", ""), None, "ข้อหา")])); t += ROW
        pg.add(row(t, [("t", "จำนวนทุนทรัพย์"), ("f", S.get("claim_baht", ""), None, "ทุนทรัพย์"), ("t", "บาท"),
                       ("f", S.get("claim_satang", ""), 48, "สตางค์"), ("t", "สตางค์")])); t += ROW
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", S.get("filer_line", ""), None, "ข้าพเจ้า")], left=168)); t += ROW
    pg.add(row(t, [("f", S.get("filer_line2", ""), None, "ข้าพเจ้า บรรทัด ๒")])); t += ROW
    jd, rd = S.get("judgment_date", {}), S.get("read_date", {})
    if kind == "appeal":
        pg.add(row(t, [("t", "ขอยื่นอุทธรณ์คัดค้าน"), ("f", S.get("against", ""), None, "คัดค้าน"), ("t", "ลงวันที่"),
                       ("f", jd.get("day", ""), 26, "ลงวันที่"), ("t", "เดือน"), ("f", jd.get("month", ""), 62, "ลงเดือน"),
                       ("t", "พ.ศ.๒๕"), ("f", yy(jd.get("year", "")), 24, "ลงปี")])); t += ROW
        pg.add(row(t, [("t", "ที่ได้อ่านเมื่อวันที่"), ("f", rd.get("day", ""), 26, "อ่านวันที่"), ("t", "เดือน"),
                       ("f", rd.get("month", ""), 70, "อ่านเดือน"), ("t", "พ.ศ. ๒๕"), ("f", yy(rd.get("year", "")), 24, "อ่านปี"),
                       ("t", "มีข้อความตามที่จะกล่าวต่อไปนี้")])); t += ROW
    elif kind == "dika":
        pg.add(row(t, [("t", "ขอยื่นฎีกาคัดค้าน"), ("f", S.get("against", ""), None, "คัดค้าน"), ("t", "ศาลอุทธรณ์ลงวันที่"),
                       ("f", jd.get("day", ""), 26, "ลงวันที่"), ("t", "เดือน"), ("f", jd.get("month", ""), 60, "ลงเดือน"),
                       ("t", "พ.ศ.๒๕"), ("f", yy(jd.get("year", "")), 24, "ลงปี")])); t += ROW
        pg.add(row(t, [("t", "ที่ได้อ่าน"), ("f", S.get("read_at", ""), None, "อ่านที่"), ("t", "เมื่อวันที่"),
                       ("f", rd.get("day", ""), 26, "อ่านวันที่"), ("t", "เดือน"), ("f", rd.get("month", ""), 62, "อ่านเดือน"),
                       ("t", "พ.ศ.๒๕"), ("f", yy(rd.get("year", "")), 24, "อ่านปี")])); t += ROW
        pg.add(text_at(t + 3, LM, "มีข้อความตามที่จะกล่าวต่อไปนี้")); t += ROW
    elif kind == "appeal_reply":
        pg.add(row(t, [("t", "ได้รับสำเนาอุทธรณ์ ของ"), ("f", S.get("of_whom", ""), None, "อุทธรณ์ของ"),
                       ("t", "แล้ว ขอทำคำแก้อุทธรณ์")])); t += ROW
        pg.add(text_at(t + 3, LM, "ยื่นต่อศาล มีข้อความตามที่จะกล่าวต่อไปนี้")); t += ROW
    else:
        pg.add(row(t, [("t", "ได้รับสำเนาฎีกา ของ"), ("f", S.get("of_whom", ""), None, "ฎีกาของ"),
                       ("t", "แล้ว ขอทำคำแก้ฎีกายื่นต่อศาล")])); t += ROW
        pg.add(text_at(t + 3, LM, "มีข้อความตามที่จะกล่าวต่อไปนี้")); t += ROW
    flows["body"] = body_flow(S["body"])
    pg.flow = ("body", t + 3, BOTTOM)
    seq += [pg, ("cont", "body", "p2")]
    tl = Page("tail")
    tl.add(circle(52, 72) + text_at(45, 100, c["tail_no"]) + text_at(67, 100, c["tail"]))
    tl.add(f'<div class="tx" style="top:100pt;left:{LM}pt;width:{RM-LM}pt;text-indent:72pt">'
           f'ข้าพเจ้าได้ยื่นสำเนา{c["doc"]} โดยข้อความถูกต้องเป็นอย่างเดียวกันมาด้วย</div>')
    tl.add(row(124, [("f", S.get("copies", ""), 60, "จำนวนสำเนา"), ("t", " ฉบับ และรอฟังคำสั่งอยู่ ถ้าไม่รอให้ถือว่าทราบแล้ว")],
               right=LM + 340))
    tl.add(sigline(195, 330, 470, S.get("signer_label", c["signer"]), S.get("signer_name")))
    drafter_block(tl, 250, c["doc"], lawyer(S, "drafter"), lawyer(S, "typist"), with_id=False)
    seq.append(tl)


def form_proxy(S, flows, seq):
    g, r = with_default(S["grantor"]), with_default(S["grantee"])
    pg = Page("first")
    t = header(pg, S, "(๑๐)", "ใบมอบฉันทะ")
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", (g["name"] + " " + g.get("role_text", "")).strip(), None, "ผู้มอบ")], left=168)); t += ROW
    pg.add(row(t, [("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", g.get("id", ""))])); t += ROW
    pg.add(row(t, [("t", "ขอมอบฉันทะให้"), ("f", r["name"], None, "ผู้รับมอบ")])); t += ROW
    pg.add(row(t, [("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", r.get("id", ""))])); t += ROW
    t = person_rows(pg, t, r, "ผู้รับมอบ", "address")
    pg.add(text_at(t + 3, LM, "ทำการแทน โดยข้าพเจ้ายอมรับผิดชอบในการที่ผู้รับมอบฉันทะของข้าพเจ้าได้ทำการไปนั้นทุกประการ")); t += ROW
    pg.add(text_at(t + 3, LM, "ในกิจการดังที่จะกล่าวต่อไปนี้")); t += ROW
    flows["tasks"] = body_flow(S["tasks"])
    pg.flow = ("tasks", t - 2, 705, {"pitch": 25, "strict": "กิจการที่มอบฉันทะยาวเกินช่องในแบบ — ย่อข้อความ"})
    pg.add(sigline(742, 120, 250, "ผู้มอบฉันทะ", g["name"]) + sigline(742, 345, 475, "ผู้รับมอบฉันทะ", r["name"]))
    pg.add(sigline(800, 120, 250, "พยาน", S.get("witness1")) + sigline(800, 345, 475, "พยาน", S.get("witness2")))
    seq.append(pg)


WCOLS = [40, 140, 122, 122, 44]


def witness_flow(ws):
    out = []
    for i, w in enumerate(ws, 1):
        w = P_(w)
        cells = [th(w.get("no", i)), rich(w.get("name", "")), rich(w.get("address1", "")), rich(w.get("address2", "")),
                 rich(w.get("note", ""))]
        out.append('<div class="keep wr">' + "".join(
            f'<div class="wc" style="width:{wd}pt">{c}</div>' for wd, c in zip(WCOLS, cells)) + "</div>")
    return "".join(out)


def witness_head(pg, top):
    labs = ["อันดับ", "ชื่อและสกุลพยาน", "บ้านเลขที่ หมู่ที่ ถนน ซอย", "ตำบล/แขวง อำเภอ/เขต จังหวัด", "หมายเหตุ"]
    x, h = LM, ""
    for w, l in zip(WCOLS, labs):
        h += f'<div class="th" style="top:{top}pt;left:{x}pt;width:{w}pt">{l}</div>'
        x += w
    pg.add(h)
    return top + 44


def form_witness_list(S, flows, seq):
    pg = Page("first")
    t = header(pg, S, "(๑๕)", "บัญชีพยาน")
    ws = S["witnesses"]
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", S.get("filer_line", ""), None, "ข้าพเจ้า")], left=168)); t += ROW
    pg.add(row(t, [("t", "ขอระบุพยานของข้าพเจ้ารวม"), ("f", th(len(ws)), 50, "จำนวนอันดับ"),
                   ("t", "อันดับ ตามบัญชีตารางข้างล่างนี้")])); t += ROW + 6
    t = witness_head(pg, t)
    flows["wit"] = witness_flow(ws)
    pg.flow = ("wit", t, 690, {"cols": WCOLS, "frame": True})
    signer = S.get("signer_label", "ผู้ระบุ")
    pg.add(sigline(735, 330, 470, signer, S.get("signer_name"), prefix=None))
    pg.add(text_at(765, LM, "หมายเหตุ กรณีเด็กอายุไม่เกิน ๑๘ ปี ให้ระบุด้วย เพื่อประสานงานในการสืบพยานเด็ก", "font-size:14pt"))
    pg.add(text_at(785, 515, "(พลิก)"))
    seq += [pg, ("cont", "wit", "wit15")]
    SIG = (signer, S.get("signer_name"))
    S["_wit_sig"] = SIG


def form_lawyer_appointment(S, flows, seq):
    pg = Page("first")
    d = S.get("date", {})
    if S.get("efiling", True):
        pg.add('<div class="efb">E-Filing</div>')
    pg.add(text_at(70, 72, "ใบแต่งทนายความ"))
    pg.add(f'<img class="garuda" src="{GARUDA}" style="top:40pt;left:250pt;width:60pt">')
    dot = "dot"
    pg.add(row(104, [("t", "คดีหมายเลขดำที่"), ("f", S.get("black_no", ""), 70, "คดีหมายเลขดำ"),
                     ("t", "/๒๕"), ("f", yy(S.get("black_year", "")), None, "ปีคดีดำ")], left=345, cls=dot))
    pg.add(row(126, [("t", "คดีหมายเลขแดงที่"), ("f", S.get("red_no", ""), 70, "คดีหมายเลขแดง"),
                     ("t", "/๒๕"), ("f", yy(S.get("red_year", "")), None, "ปีคดีแดง")], left=338, cls=dot))
    pg.add(row(156, [("t", "ศาล"), ("f", S.get("court", ""), None, "ศาล")], left=300, cls=dot))
    pg.add(row(178, [("t", "วันที่"), ("f", d.get("day", ""), 34, "วันที่"), ("t", "เดือน"),
                     ("f", d.get("month", ""), None, "เดือน"), ("t", "พุทธศักราช ๒๕"),
                     ("f", yy(d.get("year", "")), 26, "ปี")], left=265, cls=dot))
    pg.add(row(200, [("t", "ความ"), ("f", S.get("case_kind", ""), None, "ความ")], left=300, right=450, cls=dot))
    pl = [P_(x) for x in S.get("plaintiffs", [])] or [{"name": ""}]
    df = [P_(x) for x in S.get("defendants", [])] or [{"name": ""}]
    pg.add(text_at(272, 72, "ระหว่าง"))
    pg.add('<svg class="brace" style="top:236pt;left:110pt" width="16pt" height="80pt" viewBox="0 0 20 92" '
           'preserveAspectRatio="none"><path d="M17 1 C8 1 9 6 9 14 L9 38 C9 44 6 46 2 46 C6 46 9 48 9 54 L9 78 '
           'C9 86 8 91 17 91" fill="none" stroke="#000" stroke-width="1"/></svg>')
    pg.add(row(230, [("f", side_name(pl), None, "ฝ่ายโจทก์"), ("t", "โจทก์")], left=128, cls=dot))
    pg.add(row(292, [("f", side_name(df), None, "ฝ่ายจำเลย"), ("t", "จำเลย")], left=128, cls=dot))
    t = 336
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", S.get("appointer_line", ""), None, "ข้าพเจ้า")], left=72, cls=dot)); t += ROW
    lw = lawyer(S, "lawyer")
    pg.add(row(t, [("t", "ขอแต่งให้"), ("f", S.get("lawyer_name", lw.get("name", "")), None, "ทนายความ")], left=72, cls=dot)); t += ROW
    pg.add(row(t, [("t", "ใบอนุญาตว่าความเลขที่"), ("f", S.get("license", lw.get("license", "")), None, "ใบอนุญาต")],
               left=72, cls=dot)); t += ROW
    powers = S.get("powers", "ดำเนินกระบวนพิจารณาใดๆ ไปในทางจำหน่ายสิทธิของข้าพเจ้าได้ด้วย เช่น การยอมรับตามที่คู่ความ"
                             "อีกฝ่ายหนึ่งเรียกร้อง การถอนฟ้อง การประนีประนอมยอมความ การสละสิทธิหรือใช้สิทธิในการอุทธรณ์"
                             "หรือฎีกา หรือขอให้พิจารณาคดีใหม่")
    flows["pw"] = (f'<div class="noind">เป็นทนายความของข้าพเจ้าในคดีนี้ และให้มีอำนาจ <sup>*</sup> '
                   f'{rich(powers)}</div>')
    pg.flow = ("pw", t - 5, t - 5 + 3 * 22, {"pitch": 22, "rule": "dotted", "left": 72, "right": 530,
                                             "strict": "ข้อความอำนาจทนายความยาวเกิน ๓ บรรทัด"})
    t += 3 * 22 + 2
    pg.add(text_at(t, 72, "ข้าพเจ้ายอมรับผิดชอบตามที่ทนายความจะได้ดำเนินกระบวนพิจารณาต่อไปตามกฎหมาย")); t += 60
    n_sign = max(1, int(S.get("appointer_signatures", 1)))
    for i in range(n_sign):
        pg.add(sigline(t, 300, 440, "ผู้แต่งทนายความ", (S.get("appointer_names") or [None] * n_sign)[i], prefix=None)); t += 34
    t += 6
    pg.add(text_at(t, 0, "<b><u>คำรับเป็นทนายความ</u></b>", f"width:{PT_W}pt;text-align:center")); t += 22
    pg.add(text_at(t, 72, "ข้าพเจ้าขอรับเป็นทนายความเพื่อดำเนินกระบวนพิจารณาต่อไปตามหน้าที่ในกฎหมาย")); t += 55
    pg.add(sigline(t, 300, 440, "ทนายความ", prefix=None)); t += 34
    pg.add(text_at(t, 0, "<b><u>คำสั่ง</u></b>", f"width:{PT_W}pt;text-align:center")); t += 40
    pg.add(hline(t, 72, 530, "dotted")); t += 25
    pg.add(hline(t, 72, 530, "dotted")); t += 45
    pg.add(sigline(t, 300, 440, "ผู้พิพากษา", prefix=None))
    seq.append(pg)


def cb(checked=False, size=10):
    """ช่องทำเครื่องหมายในแบบ (๕๗)"""
    return f'<span class="cb{" on" if checked else ""}" style="width:{size}pt;height:{size}pt"></span>'


def form_settlement(S, flows, seq):
    """(๒๙) สัญญาประนีประนอมยอมความ"""
    pg = Page("first")
    t = header(pg, S, "(๒๙)", "สัญญาประนี", title2="ประนอม", title3="ยอมความ")
    pg.add(row(t, [("t", "เรื่อง"), ("f", S.get("subject", ""), None, "เรื่อง")], left=LM)); t += ROW
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", S.get("declarant", ""), None, "ข้าพเจ้า")], left=168)); t += ROW
    pg.add(text_at(t + 3, LM, "ขอทำสัญญาประนีประนอมยอมความต่อหน้าศาลมีข้อความตามที่จะกล่าวต่อไปนี้")); t += ROW
    sg = S.get("signers", {})
    body = list(S["body"])
    if S.get("closing") is not False:
        body += [{"keep": [{"text": "ข้าพเจ้าได้ลงลายมือชื่อไว้เป็นสำคัญ", "align": "none"},
                           {"sign": S.get("side_labels", ["โจทก์", "จำเลย"])[0], "name": sg.get("plaintiff_name")},
                           {"sign": S.get("side_labels", ["โจทก์", "จำเลย"])[1], "name": sg.get("defendant_name")},
                           {"text": "คู่ความได้ลงชื่อต่อหน้าข้าพเจ้า", "align": "none"},
                           {"sign": "ผู้พิพากษา", "name": sg.get("judge1")},
                           {"sign": "ผู้พิพากษา", "name": sg.get("judge2")}]}]
    S["_docx_body"] = body
    flows["body"] = body_flow(body)
    pg.flow = ("body", t + 3, BOTTOM)
    seq += [pg, ("cont", "body", "p2")]


def form_witness_statement(S, flows, seq):
    """(๒๑ ทวิ) บันทึกถ้อยคำแทนการซักถามพยาน"""
    w = P_(S["witness"])
    pg = Page("first")
    t = header(pg, S, "(๒๑ ทวิ)", "บันทึกถ้อยคำแทนการ", title2="ซักถามพยาน", mark=False, court_use=True)
    t += 8
    pg.add(row(t, [("t", "๑.  ข้าพเจ้าชื่อ"), ("f", w.get("name", ""), None, "ชื่อพยาน")])); t += ROW
    pg.add(row(t, [("t", "๒.  เกิดวันที่"), ("f", w.get("birth_day", ""), 40, "เกิดวันที่"), ("t", "เดือน"),
                   ("f", w.get("birth_month", ""), None, "เกิดเดือน"), ("t", "พ.ศ."),
                   ("f", w.get("birth_year", ""), 60, "เกิดปี"), ("t", "อายุ"), ("f", w.get("age", ""), 40, "อายุ"),
                   ("t", "ปี")])); t += ROW
    pg.add(row(t, [("t", "๓.  ตำแหน่งหรืออาชีพ"), ("f", w.get("occupation", ""), None, "อาชีพ")])); t += ROW
    pg.add(row(t, [("t", "๔.  ภูมิลำเนา"), ("f", w.get("domicile", ""), None, "ภูมิลำเนา")])); t += ROW
    pg.add(row(t, [("t", "๕.  เกี่ยวพันกับคู่ความ"), ("f", w.get("relation", ""), None, "เกี่ยวพันกับคู่ความ")])); t += ROW
    lead = S.get("lead", "ขอยืนยันข้อเท็จจริงหรือความเห็นของข้าพเจ้าต่อศาลแทนการซักถามว่า")
    body = [{"text": lead + " " + S["body"][0], "align": ""}] + list(S["body"][1:]) if S.get("body") else []
    sg = S.get("signers", {})
    if S.get("closing") is not False:
        body += [{"keep": [{"blank": 1},
                           {"sign": "พยาน", "name": sg.get("witness_name", w.get("name"))},
                           {"sign": S.get("side_labels", ["โจทก์", "จำเลย"])[0], "name": sg.get("plaintiff_name")},
                           {"sign": S.get("side_labels", ["โจทก์", "จำเลย"])[1], "name": sg.get("defendant_name")}]}]
    S["_docx_body"] = body
    flows["body"] = body_flow(body)
    pg.flow = ("body", t + 3, BOTTOM)
    seq += [pg, ("cont", "body", "p2")]


def form_bail_contract(S, flows, seq):
    """(๕๘) สัญญาประกัน"""
    su = with_default(S["surety"])
    S.setdefault("case_kind", "อาญา")
    S.setdefault("side_labels", ["ผู้ร้อง/โจทก์", "ผู้ต้องหา/จำเลย"])
    pg = Page("first")
    t = header(pg, S, "(๕๘)", "สัญญาประกัน")
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", su.get("name", ""), None, "ผู้ประกัน"), ("t", "ผู้ประกัน")], left=120)); t += ROW
    pg.add(row(t, [("t", "เลขประจำตัวประชาชน"), ("sp", 4), ("id", su.get("id", ""))])); t += ROW
    pg.add(text_at(t + 3, LM, "ขอทำสัญญาประกันให้ไว้ต่อศาล ดังนี้")); t += ROW
    g = lambda k, d="": S.get(k, d)
    items = [
        f'ข้อ ๑. ข้าพเจ้าสัญญารับประกันตัว [[{g("accused_name")}]] {S.get("accused_label", "ผู้ต้องหา/จำเลย")} '
        f'ในคดีเรื่องนี้เพื่อให้ศาลปล่อยชั่วคราวจนกว่าข้าพเจ้าได้นำบุคคลดังกล่าว มอบตัวต่อศาล และศาลสั่งให้ถอนประกัน หรือปล่อยตัวไป',
        f'ข้อ ๒. ในระหว่างประกันนี้ ข้าพเจ้าและ/หรือผู้ที่ได้รับการปล่อยชั่วคราวจะปฏิบัติตามคำสั่งศาล '
        f'โดยจะมารายงานตัวตามนัดหรือหมายเรียกของศาลทุกครั้ง มิฉะนั้น ข้าพเจ้ายอมรับผิดใช้เงินจำนวน '
        f'[[{g("penalty_baht")}]] บาท ([[{g("penalty_text")}]]) ให้แก่ศาลจนครบ',
        f'ข้อ ๓. ข้าพเจ้าขอวาง {g("collateral")}'
        + (f' และ/หรือเงินสด [[{g("cash_baht")}]] บาท ([[{g("cash_text")}]])' if g("cash_baht") else "")
        + ' ไว้เป็นหลักประกันต่อศาล',
        'ข้อ ๔. ข้าพเจ้าจะมาศาลตามนัดทุกนัด ถ้าไม่มาให้ถือว่าทราบคำสั่งของศาลในแต่ละนัดแล้ว',
        'ข้อ ๕. หากข้าพเจ้าผิดสัญญา ศาลมีอำนาจบังคับข้าพเจ้าให้ใช้เบี้ยปรับเต็มตามจำนวนในสัญญานี้ '
        'แล้วบังคับคดีเอาแก่ทรัพย์สินทั้งหลายของข้าพเจ้าใช้เบี้ยปรับนั้น ตลอดจนค่าฤชาธรรมเนียมและค่าเสียหายทั้งปวงอันเกี่ยวแก่การนี้',
    ]
    body = [{"text": x} for x in items] + list(S.get("extra_body", []))
    body += [{"keep": [{"sign": "ผู้ประกัน", "name": S.get("signer_name", su.get("name"))},
                       {"sign": "พยาน", "name": S.get("witness1")},
                       {"sign": "พยาน", "name": S.get("witness2")}]}]
    S["_docx_body"] = body
    flows["body"] = body_flow(body)
    pg.flow = ("body", t + 3, 700)
    seq += [pg, ("cont", "body", "p2"), bail_order_page(S)]


def bail_order_page(S):
    """หน้าคำสั่งศาลท้าย (๕๘) — ศาลกรอก สคริปต์พิมพ์แบบไว้ให้ครบชุด"""
    pg = Page("order")
    pg.add(text_at(110, 0, "<b>คำสั่ง</b>", f"width:{PT_W}pt;text-align:center"))
    pg.add(row(140, [("t", "อนุญาตให้"), ("f", "", None, "")], left=150))
    pg.add(text_at(165, LM, "เป็นผู้ประกันตามสัญญานี้"))
    pg.add(row(190, [("t", "วันที่"), ("f", "", 50, ""), ("t", "เดือน"), ("f", "", 130, ""),
                     ("t", "พุทธศักราช ๒๕"), ("f", "", 60, "")], left=150, right=480))
    pg.add(sigline(250, 330, 470, "ผู้พิพากษา", prefix=None))
    pg.add(text_at(390, 0, "รับทราบคำสั่งศาลและกำหนดนัดรายงานตัวแล้ว", f"width:{PT_W}pt;text-align:center"))
    pg.add(row(440, [("t", "นัด"), ("f", "", 210, ""), ("t", "ในวันที่"), ("f", "", None, "")], left=150))
    pg.add(sigline(520, 85, 290, S.get("accused_label", "ผู้ต้องหา / จำเลย"), prefix=None)
           + sigline(520, 330, 525, "ผู้ประกัน", prefix=None))
    return pg


BAIL_STAGES = ["สอบสวน", "พิจารณา", "อุทธรณ์/ฎีกา"]


def form_bail_request(S, flows, seq):
    """(๗) คำร้องขอปล่อยชั่วคราว ชั้นสอบสวน/พิจารณา/อุทธรณ์/ฎีกา (แบบ ๕๗)"""
    f = with_default(S["filer"])
    lab = S.get("filer_label", "ผู้ต้องหา/จำเลย")
    pg = Page("first")
    S.setdefault("case_kind", "อาญา")
    S.setdefault("side_labels", ["ผู้ร้อง/โจทก์", "ผู้ต้องหา/จำเลย"])
    t = header(pg, S, "(๗)", "คำร้องขอปล่อยชั่วคราว", title2="ชั้นสอบสวน/พิจารณา/อุทธรณ์/ฎีกา")
    R = 21
    pg.add(row(t, [("t", "ข้าพเจ้า"), ("f", f.get("name", ""), None, "ข้าพเจ้า"), ("t", lab)], left=120)); t += R
    pg.add(text_at(t, LM, "ขอยื่นคำร้องขอให้ศาลปล่อยชั่วคราว มีข้อความตามที่จะกล่าวต่อไปนี้")); t += R
    stage = S.get("stage", "")
    seg = [("h", "ข้อ ๑. ข้าพเจ้าประสงค์ขอให้ปล่อยชั่วคราวในชั้น")]
    for sname in BAIL_STAGES:
        seg += [("sp", 6), ("h", cb(stage == sname)), ("sp", 3), ("h", esc(sname))]
    pg.add(row(t, seg, left=LM + 36, right=RM)); t += R
    rel = [P_(x) for x in S.get("relatives", [])]
    pg.add(row(t, [("h", cb(bool(rel))), ("sp", 4), ("h", "โดยมีญาติหรือบุคคลที่สามารถติดต่อได้ ดังนี้")])); t += R
    for i in range(2):
        r = rel[i] if i < len(rel) else {}
        pg.add(row(t, [("t", f"{th(i + 1)}. ชื่อ"), ("f", r.get("name", ""), None, f"ญาติ{i+1}.ชื่อ"),
                       ("t", "เกี่ยวข้องเป็น"), ("f", r.get("relation", ""), 120, f"ญาติ{i+1}.เกี่ยวข้อง")],
                   left=LM + 36)); t += R
        pg.add(row(t, [("t", "หมายเลขโทรศัพท์"), ("f", r.get("phone", ""), None, f"ญาติ{i+1}.โทรศัพท์")])); t += R
    pg.add(row(t, [("h", cb(S.get("agree_conditions", True))), ("sp", 4),
                   ("h", "ข้าพเจ้ายินยอมปฏิบัติตามคำสั่งหรือเงื่อนไขใด ๆ ของศาลเพื่อป้องกันการหลบหนี หรือเหตุอันตราย หรือความ")]))
    t += R
    pg.add(text_at(t, LM, "เสียหายที่อาจเกิดขึ้นจากการปล่อยชั่วคราว รวมถึงการใช้มาตรการกำกับดูแลผู้ถูกปล่อยชั่วคราว")); t += R + 5
    pg.add(row(t, [("h", cb(S.get("agree_em", False))), ("sp", 4),
                   ("h", "ข้าพเจ้ายินยอมใช้อุปกรณ์อิเล็กทรอนิกส์สำหรับติดตามตัว (EM) ในระหว่างปล่อยชั่วคราว")])); t += R
    body = list(S.get("body", []))
    flows["body"] = body_flow(body) if body else ""
    if body:
        pg.flow = ("body", t + 4, 580)
        t = 580
    pg.add(text_at(t + 10, LM, "ขอประทานศาลได้โปรดพิจารณาคำร้องนี้ด้วย", "text-indent:72pt"))
    pg.add(text_at(t + 38, 0, "ควรมิควรแล้วแต่จะโปรด", f"width:{PT_W}pt;text-align:center"))
    pg.add(sigline(t + 70, 300, 470, S.get("signer_label", lab), S.get("signer_name"), prefix="ลงชื่อ"))
    first_page_footer(pg, esc(lab), top=690, dashed=True)
    seq.append(pg)
    if body:
        seq.append(("cont", "body", "p2"))
    if S.get("court_order_page", True):
        seq.append(bail_order_sheet(S))


def bail_order_sheet(S):
    """หน้า ๒ ของแบบ (๕๗) — คำสั่งปล่อยชั่วคราวของผู้ต้องหา/จำเลย (ศาลกรอก)"""
    pg = Page("order")
    lab = S.get("filer_label", "ผู้ต้องหา/จำเลย")
    pg.add(text_at(40, 0, f"<b>คำสั่งปล่อยชั่วคราวของ{esc(lab)}</b>", f"width:{PT_W}pt;text-align:center"))
    t = 64
    pg.add(row(t, [("h", cb(False, 11)), ("sp", 4),
                   ("h", "พิเคราะห์แล้ว อนุญาตให้ปล่อยชั่วคราวระหว่างสอบสวน/พิจารณา/อุทธรณ์/ฎีกา โดย")], left=72)); t += 24
    pg.add(row(t, [("h", cb()), ("sp", 4), ("h", f"<b>ไม่มีประกัน</b> ให้{esc(lab)} สาบาน/ปฏิญาณตนว่าจะมาศาลตามนัดหรือหมายเรียก")],
               left=100)); t += 24
    pg.add(row(t, [("h", cb()), ("sp", 4), ("h", f"<b>กำหนดเงื่อนไขให้{esc(lab)}</b> ปฏิบัติในระหว่างปล่อยชั่วคราว ดังนี้")],
               left=100)); t += 30
    for i in range(3):
        pg.add(row(t, [("t", f"{th(i + 1)}."), ("f", "", None, "")], left=150)); t += 28
    pg.add(row(t, [("h", cb()), ("sp", 4), ("t", "ให้แต่งตั้ง"), ("f", "", 230, ""),
                   ("h", "<b>เป็นผู้กำกับดูแลผู้ถูกปล่อยชั่วคราว*</b> โดยให้")], left=100)); t += 24
    pg.add(text_at(t, 72, "มีหน้าที่ดังต่อไปนี้")); t += 28
    for s in ("(   ) สอดส่องดูแลให้ปฏิบัติตามคำสั่งหรือเงื่อนไขที่ศาลกำหนด",
              f"(   ) รับรายงานตัวแทนศาล โดยให้{lab} ไปรายงานตัวต่อผู้กำกับดูแลผู้ถูกปล่อยชั่วคราว",
              "(   ) ให้คำปรึกษา และมีหนังสือแจ้งคำสั่งให้ผู้กำกับดูแลผู้ถูกปล่อยชั่วคราวทราบ"):
        pg.add(text_at(t, 150, esc(s), "font-size:15pt")); t += 22
    t += 6
    pg.add(row(t, [("h", cb()), ("sp", 4), ("h", "<b>มีประกันแต่ไม่ต้องวางหลักประกัน</b> ให้"), ("f", "", 150, ""),
                   ("h", "ทำสัญญาประกันในวงเงิน"), ("f", "", None, ""), ("t", "บาท")], left=100)); t += 26
    pg.add(row(t, [("h", cb()), ("sp", 4), ("h", "<b>ติดอุปกรณ์อิเล็กทรอนิกส์สำหรับติดตามตัว (EM)</b> โดยกำหนดเงื่อนไข"),
                   ("f", "", None, "")], left=100)); t += 30
    pg.add(row(t, [("h", cb()), ("sp", 4), ("h", "<b>มีประกันและมีเหตุจำเป็นให้เรียกหลักประกัน</b> ตีราคาประกันในวงเงิน"),
                   ("f", "", None, ""), ("t", "บาท")], left=100)); t += 26
    pg.add(text_at(t, 72, "และวางหลักประกันตามที่ศาลกำหนดก่อนปล่อยตัว")); t += 30
    pg.add(row(t, [("h", cb()), ("sp", 4), ("h", "<b>ไม่อนุญาตให้ปล่อยชั่วคราว</b> เนื่องจาก"), ("f", "", None, "")],
               left=100)); t += 26
    pg.add(hline(t, 72, RM, "dotted")); t += 30
    pg.add(text_at(t, 72, f"<b>ให้ยกคำร้องขอปล่อยชั่วคราว หมายขัง</b> แจ้งคำสั่งพร้อมเหตุผลเป็นหนังสือให้{esc(lab)}/"
                          "ผู้ร้องขอประกัน ทราบโดยเร็ว")); t += 40
    pg.add(f'<div class="frame" style="top:{t}pt;left:72pt;width:230pt;height:90pt;border-top:.6pt solid #000"></div>')
    pg.add(text_at(t + 6, 84, "ทราบคำสั่งแล้ว", "font-size:15pt"))
    pg.add(sigline(t + 46, 100, 240, "ผู้ร้องขอประกัน", prefix=None))
    pg.add(sigline(t + 74, 100, 240, S.get("filer_label", "ผู้ต้องหา/จำเลย"), prefix=None))
    pg.add(sigline(t + 46, 350, 500, "ผู้พิพากษา", prefix=None))
    pg.add(text_at(t + 110, 72, "*ถ้ายังไม่ได้กำหนดตัวผู้กำกับดูแลผู้ถูกปล่อยชั่วคราว ให้ระบุในภายหลัง", "font-size:15pt"))
    return pg


PCOLS = [50, 158, 53, 76, 53, 84]


def property_flow(items):
    out = []
    for i, it in enumerate(items, 1):
        it = P_(it)
        cells = [th(it.get("no", i)), rich(it.get("name", "")), rich(it.get("qty", "")),
                 rich(it.get("baht", "")), rich(it.get("satang", "")), rich(it.get("note", ""))]
        out.append('<div class="keep wr">' + "".join(
            f'<div class="wc wc{j}" style="width:{wd}pt">{c}</div>' for j, (wd, c) in enumerate(zip(PCOLS, cells))) + "</div>")
    return "".join(out)


def property_head(pg, top):
    """หัวตาราง (๘): ชั้นบน อันดับ | รายการ | จำนวน | ราคา (คร่อม ๒ ช่อง) | หมายเหตุ · ชั้นล่าง บาท | สตางค์"""
    x = LM
    h = ""
    for i, (w, l) in enumerate(zip(PCOLS, ["อันดับ", "รายการ", "จำนวน", "บาท", "สตางค์", "หมายเหตุ"])):
        if i in (3, 4):
            h += f'<div class="th" style="top:{top+22}pt;left:{x}pt;width:{w}pt;height:22pt">{l}</div>'
        else:
            h += f'<div class="th" style="top:{top}pt;left:{x}pt;width:{w}pt;height:44pt">{l}</div>'
        x += w
    xp = LM + sum(PCOLS[:3])
    h += f'<div class="th" style="top:{top}pt;left:{xp}pt;width:{PCOLS[3]+PCOLS[4]}pt;height:22pt;border-bottom:none">ราคา</div>'
    pg.add(h)
    return top + 44


def form_property_list(S, flows, seq):
    """(๘) บัญชีทรัพย์"""
    pg = Page("first")
    items = S.get("items", [])
    pg.add(circle(68, 86) + text_at(62, 112, "(๘)"))
    pg.add(f'<img class="garuda" src="{GARUDA}" style="top:58pt;left:266pt;width:62pt">')
    pg.add(row(122, [("t", "คดีหมายเลขดำที่"), ("f", S.get("black_no", ""), 70, "คดีหมายเลขดำ"), ("t", "/๒๕"),
                     ("f", yy(S.get("black_year", "")), None, "ปีคดีดำ")], left=360))
    pg.add(row(149, [("t", "คดีหมายเลขแดงที่"), ("f", S.get("red_no", ""), 70, "คดีหมายเลขแดง"), ("t", "/๒๕"),
                     ("f", yy(S.get("red_year", "")), None, "ปีคดีแดง")], left=353))
    pg.add(text_at(176, 0, S.get("title", "บัญชีทรัพย์"), f"width:{PT_W}pt;text-align:center"))
    top = property_head(pg, 200)
    flows["prop"] = property_flow(items)
    pg.flow = ("prop", top, 700, {"cols": PCOLS, "frame": True, "pitch": float(S.get("row_pitch_pt", 32))})
    pg.add(f'<div class="frame" style="top:700pt;left:{LM}pt;width:{RM-LM}pt;height:32pt;border-top:.6pt solid #000"></div>')
    pg.add(text_at(702, LM + 8, "<b>รวม</b>"))
    pg.add(text_at(702, LM + sum(PCOLS[:3]), esc(S.get("total_baht", "")), f"width:{PCOLS[3]}pt;text-align:center"))
    pg.add(text_at(702, LM + sum(PCOLS[:4]), esc(S.get("total_satang", "")), f"width:{PCOLS[4]}pt;text-align:center"))
    pg.add(sigline(770, 330, 505, S.get("signer_label", "ผู้ยื่น"), S.get("signer_name"), prefix=None))
    seq.append(pg)
    S["_prop_head"] = True



# ---------------------------------------------------------------- ไฟล์ Word (.docx)
# เขียน XML เองเพื่อให้เปิดใน MS Office 2016 ได้ตรงรูปแบบที่สำนักงานกำหนด และไม่ต้องติดตั้งไลบรารีใด
# TH SarabunPSK 16 pt (ตั้ง w:cs และ w:szCs ด้วย ไม่งั้น Word สลับเป็น Cordia New 14 pt)
# ภาษา th-TH ทั้ง val/eastAsia/bidi เพื่อให้ Word ตัดคำไทยเอง · จัดชิดขอบแบบ both (ไม่ใช่ thaiDistribute)
# A4 ขอบซ้าย 3 ขวา 2 บน 2.5 ล่าง 2 ซม. ย่อหน้าแรก 2.54 ซม. · ไม่ตั้งระยะบรรทัดแบบ exact (สระบนล่างจะถูกตัด)
import zipfile, time

TW = 566.93          # 1 ซม. = 566.93 twip
DOCX_FONT = "TH SarabunPSK"


def _rpr(b=False, u=False, s=False, sz=32):
    x = f'<w:rFonts w:ascii="{DOCX_FONT}" w:hAnsi="{DOCX_FONT}" w:cs="{DOCX_FONT}"/>'
    if b: x += "<w:b/><w:bCs/>"
    if u: x += '<w:u w:val="single"/>'
    if s: x += "<w:strike/>"
    return f"<w:rPr>{x}<w:sz w:val=\"{sz}\"/><w:szCs w:val=\"{sz}\"/></w:rPr>"


def _xml_esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _runs(text):
    """แปลงมาร์กอัป **หนา** __ขีดเส้นใต้__ ~~ขีดฆ่า~~ [[ไม่ตัดบรรทัด]] เป็น run ของ Word"""
    import re as _re
    out, pos = [], 0
    pat = _re.compile(r"\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|\[\[(.+?)\]\]")
    for m in pat.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False, False, False))
        g = m.lastindex
        out.append((m.group(g), g == 1, g == 2, g == 3))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False, False, False))
    if not out:
        out = [("", False, False, False)]
    return "".join(f'<w:r>{_rpr(b, u, s)}<w:t xml:space="preserve">{_xml_esc(t)}</w:t></w:r>'
                   for t, b, u, s in out)


def _p(text="", align="both", indent=0, b=False, space_after=0):
    pr = f'<w:pPr><w:jc w:val="{align}"/>'
    if indent:
        pr += f'<w:ind w:firstLine="{int(indent)}"/>'
    pr += f'<w:spacing w:after="{int(space_after)}" w:line="240" w:lineRule="auto"/></w:pPr>'
    body = _runs(f"**{text}**" if b and text else text)
    return f"<w:p>{pr}{body}</w:p>"


def _sig(label, name=None):
    out = [_p(), _p("ลงชื่อ ....................................................... " + label, "right")]
    if name:
        out.append(_p(f"({name})", "right"))
    return "".join(out)


def docx_body(S, title):
    """เนื้อหา .docx ของทุกแบบ: หัวคดี → ย่อหน้าเปิด → เนื้อความ → ลงชื่อ
    เป็นร่างสำหรับแก้ต่อและพิมพ์ลงแบบพิมพ์ ไม่ใช่การวาดแบบพิมพ์ (ฉบับยื่นศาลใช้ PDF)"""
    d = S.get("date", {})
    pl = [P_(x)["name"] for x in S.get("plaintiffs", [])]
    df = [P_(x)["name"] for x in S.get("defendants", [])]
    lab = S.get("side_labels", ["โจทก์", "จำเลย"])
    ps = [_p(title, "center", b=True), _p()]
    hd = []
    if S.get("court"):
        hd.append(f'ศาล{S["court"]}')
    if S.get("black_no"):
        hd.append(f'คดีหมายเลขดำที่ {S["black_no"]}/๒๕{yy(S.get("black_year", ""))}')
    if S.get("red_no"):
        hd.append(f'คดีหมายเลขแดงที่ {S["red_no"]}/๒๕{yy(S.get("red_year", ""))}')
    if d.get("day"):
        hd.append(f'วันที่ {d.get("day","")} เดือน{d.get("month","")} พุทธศักราช ๒๕{yy(d.get("year",""))}')
    if S.get("case_kind"):
        hd.append(f'ความ{S["case_kind"]}')
    for x in hd:
        ps.append(_p(x, "right"))
    if pl or df:
        ps.append(_p())
        if pl:
            ps.append(_p(f'{"  ".join(pl)}  {lab[0]}', "center"))
        if df:
            ps.append(_p("ระหว่าง", "center"))
            ps.append(_p(f'{"  ".join(df)}  {lab[1]}', "center"))
    ps.append(_p())
    for x in S.get("_docx_lead", []):
        ps.append(_p(x, indent=TW * 2.54 / 1))
    for item in S.get("_docx_body", S.get("body", [])):
        if isinstance(item, str):
            ps.append(_p(item, indent=int(TW * 2.54)))
        elif isinstance(item, dict):
            if "text" in item:
                al = {"center": "center", "right": "right", "none": "both"}.get(item.get("align", ""), "both")
                ps.append(_p(item["text"], al, indent=0 if item.get("align") else int(TW * 2.54)))
            elif "sign" in item:
                ps.append(_sig(item["sign"], item.get("name")))
            elif "keep" in item:
                for k in item["keep"]:
                    if isinstance(k, str):
                        ps.append(_p(k, indent=int(TW * 2.54)))
                    elif "sign" in k:
                        ps.append(_sig(k["sign"], k.get("name")))
                    elif "text" in k:
                        al = {"center": "center", "right": "right"}.get(k.get("align", ""), "both")
                        ps.append(_p(k["text"], al, indent=0 if k.get("align") else int(TW * 2.54)))
    for s in S.get("_docx_tail", []):
        ps.append(_p(s, "center"))
    return "".join(ps)


DOCX_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

DOCX_CT = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

DOCX_STYLES = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="{DOCX_FONT}" w:hAnsi="{DOCX_FONT}" w:cs="{DOCX_FONT}" w:eastAsia="{DOCX_FONT}"/>
<w:sz w:val="32"/><w:szCs w:val="32"/>
<w:lang w:val="th-TH" w:eastAsia="th-TH" w:bidi="th-TH"/>
</w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:jc w:val="both"/><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
</w:styles>'''


DOC_TITLES = {
    "complaint": "คำฟ้อง", "request": None, "answer": "คำให้การจำเลย", "appeal": "อุทธรณ์",
    "appeal_reply": "คำแก้อุทธรณ์", "dika": "ฎีกา", "dika_reply": "คำแก้ฎีกา", "proxy": "ใบมอบฉันทะ",
    "witness_list": "บัญชีระบุพยาน", "lawyer_appointment": "ใบแต่งทนายความ", "property_list": "บัญชีทรัพย์",
    "witness_statement": "บันทึกถ้อยคำแทนการซักถามพยาน", "settlement": "สัญญาประนีประนอมยอมความ",
    "bail_request": "คำร้องขอปล่อยชั่วคราว", "bail_contract": "สัญญาประกัน",
}


def docx_title(S):
    form = S.get("form", "complaint")
    return DOC_TITLES.get(form) or S.get("kind", "คำร้อง")


def write_docx(S, path, title):
    sect = (f'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            f'<w:pgMar w:top="{int(2.5*TW)}" w:right="{int(2*TW)}" w:bottom="{int(2*TW)}" w:left="{int(3*TW)}"'
            f' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{docx_body(S, title)}{sect}</w:body></w:document>')
    zi = lambda n: zipfile.ZipInfo(n, date_time=time.localtime()[:6])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zi("[Content_Types].xml"), DOCX_CT)
        z.writestr(zi("_rels/.rels"), DOCX_RELS)
        z.writestr(zi("word/document.xml"), doc)
        z.writestr(zi("word/styles.xml"), DOCX_STYLES)
    return path

# ---------------------------------------------------------------- CSS / JS
CSS = """
@page { size: A4; margin: 0 }
* { box-sizing: border-box }
html, body { margin: 0; padding: 0; background: #fff }
body { font-family: %(ff)s; font-size: 16pt; color: #000; -webkit-print-color-adjust: exact; print-color-adjust: exact }
.page { position: relative; width: 595.28pt; height: 841.89pt; overflow: hidden; break-after: page }
.page:last-child { break-after: auto }
.row { position: absolute; display: flex; align-items: flex-end; height: 22pt; line-height: 22pt; white-space: nowrap; gap: 3pt }
.lb { flex: 0 0 auto; border-bottom: .6pt solid transparent }
.f { border-bottom: .6pt solid #555; overflow: hidden; padding: 0 3pt; min-width: 0 }
.row.dot .f { border-bottom: 1pt dotted #333 }
.f.c { text-align: center; padding: 0 }
.f .v { display: inline-block }
.tx { position: absolute; line-height: 22pt; white-space: nowrap }
.circ { position: absolute; width: 11pt; height: 11pt; border: .8pt solid #000; border-radius: 50%% }
.garuda, .brace { position: absolute }
.ids { display: inline-flex; align-items: center; flex: 0 0 auto; margin-bottom: 2pt }
.box { display: inline-block; width: 12.5pt; height: 15pt; border: .6pt solid #000; line-height: 15pt; text-align: center; font-size: 14pt; margin: 0 .8pt }
.dash { padding: 0 1.5pt; line-height: 15pt }
.sig { position: absolute; border-bottom: .6pt dotted #000; height: 0 }
.hl { position: absolute; height: 0; border-top: .6pt solid #000 }
.win { position: absolute }
.clip { position: absolute; left: 0; top: 0; width: 100%%; overflow: hidden }
.rl { position: absolute; left: 0; right: 0; height: 0; border-top: .5pt solid #777 }
.vl { position: absolute; top: 0; bottom: 0; width: 0; border-left: .6pt solid #000 }
.flowcopy, .flowsrc { position: absolute; left: 0; width: 100%%; line-height: var(--P); text-align: left }
.flowsrc { visibility: hidden; top: 0 }
.par { text-indent: 72pt }
.noind { text-indent: 0 }
.ctr { text-align: center }
.rgt { text-align: right }
.blank { height: var(--P) }
.sg { display: flex; justify-content: flex-end; align-items: flex-end; height: var(--P); gap: 4pt; padding-right: 20pt }
.sgl { display: inline-block; width: 150pt; border-bottom: .6pt dotted #000; height: 0; margin-bottom: 9pt }
.sgn { text-align: right; padding-right: 60pt }
.pl .pk { display: inline-block; width: 72pt }
.pd { padding-left: 72pt }
.nb { white-space: nowrap }
.hd { position: absolute; line-height: 22pt }
.wr { display: flex }
.wc { padding: 0 3pt; overflow-wrap: anywhere }
.wc:first-child { text-align: center }
.th { position: absolute; height: 44pt; border: .6pt solid #000; margin-left: -.3pt; display: flex; align-items: center;
      justify-content: center; text-align: center; line-height: 18pt; font-size: 15pt; padding: 0 2pt; white-space: normal }
.frame { position: absolute; border: .6pt solid #000; border-top: none }
.efb { position: absolute; top: 40pt; left: 455pt; width: 70pt; height: 26pt; border: 1pt solid #000; text-align: center;
       line-height: 26pt; font-family: Arial, sans-serif; font-size: 11pt; font-weight: bold }
sup { font-size: 11pt; line-height: 0 }
.cb { display: inline-block; border: 1pt solid #000; margin-bottom: 2pt; flex: 0 0 auto }
.cb.on::after { content: "\\2713"; display: block; line-height: 9pt; text-align: center; font-size: 11pt }
.wc2, .wc3, .wc4 { text-align: center }
"""

# แบ่งคำไทยด้วย Intl.Segmenter แล้วกระจายช่องว่างที่เหลือของแต่ละบรรทัดตามรอยต่อคำ (เว้นวรรคได้น้ำหนักมากกว่า)
# บรรทัดสุดท้ายของย่อหน้าชิดซ้าย  และคืนตำแหน่งก้อน .keep (ห้ามแยกข้ามหน้า) เป็นหน่วยบรรทัด
MEASURE_JS = r"""
(P) => {
  const seg = new Intl.Segmenter('th', {granularity: 'word'});
  const out = {};
  for (const src of document.querySelectorAll('.flowsrc')) {
    const Ppx = parseFloat(getComputedStyle(src).lineHeight);
    for (const par of src.querySelectorAll('.par, .noind')) {
      par.querySelectorAll('.nb').forEach(n => { if (!n.parentElement.closest('.nb')) n.classList.add('w'); });
      const tw = document.createTreeWalker(par, NodeFilter.SHOW_TEXT,
        {acceptNode: n => n.parentElement.closest('.nb') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
      const nodes = [];
      while (tw.nextNode()) nodes.push(tw.currentNode);
      for (const tn of nodes) {
        const fr = document.createDocumentFragment();
        for (const s of seg.segment(tn.data)) {
          const sp = document.createElement('span');
          sp.className = /^\s+$/.test(s.segment) ? 'ws' : 'w'; sp.textContent = s.segment; fr.appendChild(sp);
        }
        tn.replaceWith(fr);
      }
      const h0 = par.getBoundingClientRect().height;
      const lines = []; let cur = null, lt = null;
      for (const s of par.querySelectorAll('.w:not(.w .w), span.ws')) {
        const r = s.getClientRects(); if (!r.length) continue;
        if (lt === null || Math.abs(r[0].top - lt) > 3) { cur = []; lines.push(cur); lt = r[0].top; }
        cur.push(s);
      }
      const pr = par.getBoundingClientRect();
      lines.slice(0, -1).forEach(L => {
        let e = L.length; while (e > 0 && L[e - 1].className === 'ws') e--;
        const it = L.slice(0, e); if (it.length < 2) return;
        const free = pr.right - it[it.length - 1].getBoundingClientRect().right - 0.8;
        if (free <= 0 || free > pr.width * 0.35) return;
        const gaps = it.slice(0, -1);
        const nsp = gaps.filter(g => g.className === 'ws').length;
        // มีเว้นวรรคพอ ให้ยืดเฉพาะที่เว้นวรรค (ไม่แทรกช่องไฟกลางคำประสม) มิฉะนั้นกระจายทุกรอยต่อคำ โดยเว้นวรรคได้น้ำหนักมากกว่า
        const spaceOnly = nsp >= 2 && free / nsp <= 14;
        const w = gaps.map(g => g.className === 'ws' ? 4 : (spaceOnly ? 0 : 1));
        const tot = w.reduce((a, b) => a + b, 0);
        gaps.forEach((g, i) => { g.style.marginRight = (free * w[i] / tot).toFixed(3) + 'px'; });
      });
      if (Math.abs(par.getBoundingClientRect().height - h0) > 1)
        par.querySelectorAll('.w, span.ws').forEach(s => s.style.marginRight = '');
    }
    const top0 = src.getBoundingClientRect().top;
    const keeps = [...src.querySelectorAll(':scope > .keep')].map(k => {
      const r = k.getBoundingClientRect();
      return [Math.round((r.top - top0) / Ppx), Math.round((r.bottom - top0) / Ppx)];
    });
    out[src.id.slice(2)] = {html: src.innerHTML, n: Math.round(src.getBoundingClientRect().height / Ppx), keeps};
  }
  return out;
}"""


def assemble(S):
    global GARUDA
    gp = os.path.join(ASSETS, "garuda.png")
    GARUDA = data_uri(gp, "image/png") if os.path.exists(gp) else "data:image/png;base64," + GARUDA_B64
    NAMES[:] = [P_(x)["name"] for k in ("plaintiffs", "defendants") for x in S.get(k, []) if P_(x).get("name")]
    flows, seq = {}, []
    form = S.get("form", "complaint")
    if form == "complaint":
        form_complaint(S, flows, seq)
    elif form == "request":
        form_request(S, flows, seq)
    elif form == "answer":
        form_answer(S, flows, seq)
    elif form in APPEAL:
        form_appeal(S, flows, seq, form)
    elif form == "proxy":
        form_proxy(S, flows, seq)
    elif form == "witness_list":
        form_witness_list(S, flows, seq)
    elif form == "lawyer_appointment":
        form_lawyer_appointment(S, flows, seq)
    elif form == "property_list":
        form_property_list(S, flows, seq)
    elif form == "witness_statement":
        form_witness_statement(S, flows, seq)
    elif form == "settlement":
        form_settlement(S, flows, seq)
    elif form == "bail_request":
        form_bail_request(S, flows, seq)
    elif form == "bail_contract":
        form_bail_contract(S, flows, seq)
    else:
        raise SystemExit(f"ไม่รู้จักแบบ {form} — ใช้ --list ดูแบบที่รองรับ")
    return flows, seq


def chunk(info, start, cap):
    """จำนวนบรรทัดที่ลงหน้าได้จริง โดยไม่ตัดกลางก้อน keep (ลายมือชื่อ คู่ความแต่ละคน แถวพยาน)"""
    n = info["n"]
    end = min(start + cap, n)
    if end < n:
        for a, b in info["keeps"]:
            if a < end < b and a > start and (b - a) <= cap:
                end = a
    return end - start


def render(spec_path, out_pdf, out_html=None, out_docx=None):
    from playwright.sync_api import sync_playwright
    S = json.load(open(spec_path, encoding="utf-8"))
    P = float(S.get("line_pitch_pt", 32))
    flows, seq = assemble(S)
    if out_docx:
        write_docx(S, out_docx, docx_title(S))
    fdir = font_dir()
    fset = next((fs for fs in FONT_SETS if os.path.exists(os.path.join(fdir, fs[0][0]))), None)
    if fset is None:
        raise SystemExit(f"ไม่พบไฟล์ฟอนต์ TH Sarabun ใน {fdir}")
    font_css = "".join(
        f"@font-face{{font-family:'CourtThai';src:url('{data_uri(os.path.join(fdir, fn), 'font/ttf')}');"
        f"font-weight:{w};font-style:{st}}}" for fn, w, st in fset if os.path.exists(os.path.join(fdir, fn)))
    css = font_css + CSS % {"ff": FONT_FAMILY} + f":root{{--P:{P}pt}}"

    # ความกว้างและระยะบรรทัดของแต่ละ flow ตามหน้าต่างแรกที่ใช้
    geo = {}
    for it in seq:
        if isinstance(it, Page) and it.flow:
            fid, top, bot, *o = it.flow
            o = o[0] if o else {}
            geo[fid] = (o.get("left", LM), o.get("right", RM), o.get("pitch", P))
    for k in flows:
        geo.setdefault(k, (LM, RM, P))

    def doc(inner):
        return (f'<!doctype html><html lang="th"><head><meta charset="utf-8"><style>{css}</style></head>'
                f'<body>{inner}</body></html>')

    errors = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page()
        meas = "".join(f'<div class="flowsrc" id="m_{k}" style="width:{geo[k][1]-geo[k][0]}pt;position:relative;'
                       f'--P:{geo[k][2]}pt">{v}</div>' for k, v in flows.items())
        pg.set_content(doc(meas))
        pg.evaluate("document.fonts.ready")
        info = pg.evaluate(MEASURE_JS, P)
        flows = {k: info[k]["html"] for k in flows}
        if os.environ.get("COURT_FORM_DEBUG"):
            print({k: (v["n"], v["keeps"]) for k, v in info.items()}, file=sys.stderr)
        pages_html, pageno, used = [], 0, {}

        def place(page, fid, top, bot, o):
            Pp = o.get("pitch", P)
            cap = int((bot - top) // Pp)
            st = used.get(fid, 0)
            n = chunk(info[fid], st, cap) if st < info[fid]["n"] else 0
            if o.get("strict") and info[fid]["n"] > cap:
                errors.append(f'{o["strict"]} (ยาว {info[fid]["n"]} บรรทัด รับได้ {cap})')
            page.add(window(fid, top, st, cap, n, Pp, o.get("left", LM), o.get("right", RM),
                            o.get("rule", "solid"), o.get("cols")))
            if o.get("frame"):
                page.add(f'<div class="frame" style="top:{top}pt;left:{LM}pt;width:{RM-LM}pt;height:{cap*Pp}pt"></div>')
            used[fid] = st + n

        for it in seq:
            if isinstance(it, Page):
                pageno += 1
                if it.flow:
                    fid, top, bot, *o = it.flow
                    place(it, fid, top, bot, o[0] if o else {})
                pages_html.append(it.html())
                continue
            _, fid, style = it
            first = True
            while used.get(fid, 0) < info[fid]["n"] or (first and style == "list"):
                pageno += 1
                px = Page("p40")
                o = {}
                if style == "p2" and pageno == 2:
                    px.add(f'<div class="hd" style="top:38pt;left:0;width:{PT_W}pt;text-align:center">{th(pageno)}</div>')
                    top, bot = 72, BOTTOM
                elif style == "wit15":
                    px.add(circle(45, 72) + text_at(38, 100, "(๑๕ ทวิ)") + text_at(60, 100, "บัญชีพยาน"))
                    px.add(row(86, [("t", "คดีหมายเลขดำที่"), ("f", S.get("black_no", ""), 70), ("t", "/๒๕"),
                                    ("f", yy(S.get("black_year", "")), None)], left=360))
                    px.add(row(110, [("t", "คดีหมายเลขแดงที่"), ("f", S.get("red_no", ""), 70), ("t", "/๒๕"),
                                     ("f", yy(S.get("red_year", "")), None)], left=353))
                    top = witness_head(px, 150)
                    bot = 730
                    o = {"cols": WCOLS, "frame": True}
                    lab, nm = S.get("_wit_sig", ("ผู้ระบุ", None))
                    px.add(sigline(770, 330, 470, lab, nm, prefix=None))
                else:
                    px.add(f'<div class="hd" style="top:52pt;left:72pt">(๔๐ ก.)</div>')
                    if style != "list":
                        px.add(f'<div class="hd" style="top:52pt;left:0;width:{PT_W}pt;text-align:center">{th(pageno)}</div>')
                    top, bot = 96, BOTTOM
                place(px, fid, top, bot, o)
                pages_html.append(px.html())
                first = False

        body_html = re.sub(r'(<div class="flowcopy" data-flow="([^"]+)"[^>]*>)(</div>)',
                           lambda m: m.group(1) + flows[m.group(2)] + m.group(3), "".join(pages_html))
        # ระยะบรรทัดเฉพาะ flow
        for k, (l, r, pp) in geo.items():
            if pp != P:
                body_html = body_html.replace(f'data-flow="{k}" style="', f'data-flow="{k}" style="--P:{pp}pt;')
        pg.set_content(doc(body_html))
        pg.evaluate("document.fonts.ready")
        bad = pg.evaluate("""() => { const bad = [];
          document.querySelectorAll('.f').forEach(f => { const v = f.querySelector('.v');
             if (v && v.offsetWidth > f.clientWidth + 1) bad.push((f.dataset.name || 'ช่อง') + ' = "' + v.textContent + '"'); });
          return bad; }""")
        if out_html:
            open(out_html, "w", encoding="utf-8").write(pg.content())
        pg.pdf(path=out_pdf, prefer_css_page_size=True, print_background=True)
        br.close()
    if bad or errors:
        print("คำเตือน — ต้องแก้ก่อนส่งไฟล์:")
        for b in bad:
            print("  - ข้อความยาวเกินช่อง:", b)
        for e in errors:
            print("  -", e)
        return 2
    print(f"สร้าง {out_pdf} แล้ว ({pageno} หน้า)")
    if out_docx:
        print(f"สร้าง {out_docx} แล้ว (ร่างสำหรับแก้ต่อ ไม่ใช่แบบพิมพ์ศาล)")
    return 0


# ตราครุฑในหัวแบบ (ฝังไว้ในไฟล์ เพื่อให้สคริปต์ไฟล์เดียวทำงานได้โดยไม่ต้องมีไฟล์ประกอบ)
GARUDA_B64 = (
    "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"
)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?"); ap.add_argument("out", nargs="?"); ap.add_argument("--html")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--docx", nargs="?", const=True, default=None,
                    help="ออกไฟล์ Word ควบคู่ (ไม่ระบุชื่อ = ชื่อเดียวกับ PDF) — ร่างสำหรับแก้ต่อ ไม่ใช่แบบพิมพ์ศาล")
    a = ap.parse_args()
    if a.list or not a.spec:
        for k, v in FORMS.items():
            print(f"{k:20s} {v}")
        sys.exit(0)
    dx = a.docx if isinstance(a.docx, str) else (re.sub(r"\.pdf$", "", a.out or "out") + ".docx" if a.docx else None)
    sys.exit(render(a.spec, a.out, a.html, dx))
