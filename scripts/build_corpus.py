#!/usr/bin/env python3
"""
Build a structured corpus of the Sangitaratnakara (Sarngadeva, 13th c.)
from Sanskrit Wikisource (sa.wikisource.org, CC-BY-SA).

Source note: the Wikisource text is a transcription of the Peter Freund
e-text. Dandas were largely stripped in that transmission, so verse
boundaries are marked by a BARE trailing Devanagari numeral, not by
the conventional "|| N ||". Four of the seven chapter pages contain
no double-danda at all.

Only sa.wikisource.org is used. Copyrighted critical editions
(Adyar 1943, Shringy & Sharma 1978) are deliberately out of scope.
"""
import json, re, time, pathlib, datetime, unicodedata
import requests
from indic_transliteration import sanscript

API = 'https://sa.wikisource.org/w/api.php'
UA = ('SangitaRatnakara-Corpus/1.0 (research corpus build; '
      'contact: yellamraju.susmita@gmail.com)')
PREFIX = 'सङ्गीतरत्नाकरः'
ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / 'raw', ROOT / 'by-chapter'
RETRIEVED = datetime.date.today().isoformat()

# Chapter order is taken from the work's own index page, not guessed.
CHAPTER_ORDER = [
    'स्वरगताध्यायः', 'रागविवेकाध्यायः', 'प्रकीर्णकाध्यायः', 'प्रबन्धाध्यायः',
    'तालाध्यायः', 'वाद्याध्यायः', 'नर्तनाध्यायः', 'परिशिष्ट १',
]
SLUGS = {
    'स्वरगताध्यायः': 'svaragatadhyaya', 'रागविवेकाध्यायः': 'ragavivekadhyaya',
    'प्रकीर्णकाध्यायः': 'prakirnakadhyaya', 'प्रबन्धाध्यायः': 'prabandhadhyaya',
    'तालाध्यायः': 'taladhyaya', 'वाद्याध्यायः': 'vadyadhyaya',
    'नर्तनाध्यायः': 'nartanadhyaya', 'परिशिष्ट १': 'parishishta_1',
}

# Cache filenames are ASCII slugs, not Devanagari, so raw/ stays greppable and
# portable across filesystems with different Unicode normalisation.
INDEX_SLUG = 'sangitaratnakara_index'


def cache_slug(title: str) -> str:
    if title == PREFIX:
        return INDEX_SLUG
    sub = title.split('/', 1)[1] if '/' in title else title
    return 'sangitaratnakara__' + SLUGS.get(sub, sub.replace(' ', '_'))


DEV_DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')
RE_TRAIL_NUM = re.compile(r'([०-९]+)\s*$')
RE_SECTION = re.compile(r"^'''\s*(अथ|इति)")

review: list[str] = []


def log_review(chapter, kind, detail, context):
    review.append(f'### {chapter} — {kind}\n\n{detail}\n\n```\n{context}\n```\n')


# ---------------------------------------------------------------- fetching
def fetch_all():
    s = requests.Session()
    s.headers['User-Agent'] = UA
    RAW.mkdir(exist_ok=True)
    pages = s.get(API, params={'action': 'query', 'list': 'allpages',
                               'apprefix': PREFIX, 'aplimit': '50',
                               'format': 'json'}).json()['query']['allpages']
    titles = [p['title'] for p in pages]
    for t in titles:
        slug = cache_slug(t)
        wt = RAW / f'{slug}.wikitext'
        if wt.exists():
            continue
        j = s.get(API, params={'action': 'parse', 'page': t,
                               'prop': 'wikitext|revid', 'format': 'json'}).json()
        (RAW / f'{slug}.json').write_text(json.dumps(j, ensure_ascii=False, indent=1))
        wt.write_text(j['parse']['wikitext']['*'])
        print(f'  fetched {t}')
        time.sleep(1)          # Wikimedia API etiquette: ~1 req/sec
    return titles


# ------------------------------------------------------------ markup strip
def clean(line: str) -> str:
    """Remove MediaWiki markup. The Sanskrit text itself is never altered."""
    s = line.strip()
    s = re.sub(r'^:+', '', s).strip()
    s = re.sub(r"'''?", '', s)
    s = re.sub(r'<br\s*/?>', '', s, flags=re.I)
    s = re.sub(r'<ref[^>]*>.*?</ref>', '', s, flags=re.I | re.S)
    s = re.sub(r'<ref[^>]*/>', '', s, flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'\[\[[^\]|]*\|([^\]]*)\]\]', r'\1', s)
    s = re.sub(r'\[\[([^\]]*)\]\]', r'\1', s)
    s = re.sub(r'\{\{[^}]*\}\}', '', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Cf')  # ZWJ/ZWNJ/etc
    return re.sub(r'[ \t]+', ' ', s).strip()


def dnum(s: str) -> int:
    return int(s.translate(DEV_DIGITS))


# ------------------------------------------------------- number classifier
#
# A trailing numeral in this text means one of FOUR things. Getting this
# wrong is the single biggest correctness risk in the whole pipeline:
#
#   1. a verse number continuing the running count       -> emit a verse
#   2. a reset to 1 at a prakarana boundary the source
#      failed to mark with a header                      -> new prakarana
#   3. a row index in an embedded table (tala matra
#      grids, ragaviveka svara notation)                 -> not a verse
#   4. a transcription error (dropped digit, off-by-one) -> verse + flag
#
# Discriminator for 2 vs 3: a real boundary is followed by a SUSTAINED
# monotonic run (1,2,3,4...). A table row index is not.
LOOKAHEAD = 8
MIN_RUN = 4          # numbered items that must line up to call it a boundary
GAP_TOLERANCE = 3    # accept n slightly ahead of expected (merged verses)

# Calibrated on the text itself: metrical Sanskrit averages 5.5-8.5 chars per
# whitespace token (long nominal compounds). Embedded notation -- tala matra
# grids, ragaviveka svara sequences, syllable-per-column layouts -- averages
# 1.4-2.6, because every token is a one- or two-syllable svara name.
NOTATION_MEAN_TOKEN_LEN = 4.0
NOTATION_MIN_TOKENS = 4

# Taladhyaya's prastara sections label their grids with a bare structural word
# plus an index ("vastu 1", "sirsakam", "talika"). Read as verses those forge
# a numbering reset and destroy the rest of the chapter. A real verse or
# hemistich in this text is never this short.
MIN_VERSE_TOKENS = 3
MIN_VERSE_CHARS = 25
STRUCTURAL_LABELS = {'वस्तु', 'शीर्षकम्', 'तालिका', 'प्रतिशाखा', 'शाखा',
                     'प्रस्तारः', 'संता', 'उपोहनम्'}


# Svara names form a closed alphabet, so a token built only from them is
# notation. Ordinary Sanskrit breaks the pattern on its first foreign
# consonant ("matah" fails at "ta"), which makes the verse/notation seam
# findable inside a single line.
RE_SVARA_TOKEN = re.compile(r'(?:स|रि|ग|म|प|ध|नि|रे|सा|री|गा|मा|पा|धा|नी)+ँ?ं?$')


def split_verse_notation(text):
    """Split "<sloka> <svara illustration>" into its two halves.

    Returns (verse, notation_or_None). Anchored on a RUN of svara tokens at
    the end of the line: a lone short token like "gama" is also a real word,
    so a single token only counts if it is long enough to be unambiguous.
    """
    toks = text.split()
    i = len(toks)
    while i > 0 and (toks[i - 1] in ('।', '॥') or RE_SVARA_TOKEN.fullmatch(toks[i - 1])):
        i -= 1
    head, tail = toks[:i], toks[i:]
    svara = [t for t in tail if t not in ('।', '॥')]
    if not head or not svara:
        return text, None
    verse = ' '.join(head).strip(' ।॥')
    if len(verse) < MIN_VERSE_CHARS:
        return text, None
    # A lone short svara token is ambiguous ("gama" is also a word). It counts
    # as notation only when what precedes it is unmistakably a verse, or when a
    # danda sits right before it -- the danda closes the verse, so whatever
    # follows cannot be part of it.
    if len(svara) < 2 and len(svara[0]) < 5:
        # The walk-back consumes the danda into `tail`, so look for it there.
        after_danda = any(t in ('।', '॥') for t in tail)
        if not after_danda and not is_verse_substance(verse):
            return text, None
    return verse, ' '.join(tail).strip(' ।॥')


def is_verse_substance(text: str) -> bool:
    """Real metrical Sanskrit, as opposed to notation or a stray label."""
    toks = [t for t in text.split() if t not in ('।', '॥')]
    if len(toks) < 4 or len(text) < 40:
        return False
    svara = sum(bool(RE_SVARA_TOKEN.fullmatch(t)) for t in toks)
    return svara / len(toks) < 0.5


RE_BEAT_TOKEN = re.compile(r'ऽ[ऽए!]*')      # beat mark, not the avagraha in a word
# Every chapter page opens with a bold wikilink back to the work's index page.
# clean() reduces it to the bare title, which would otherwise be buffered as
# the first hemistich of verse 1.
RE_TITLE_BANNER = re.compile(r"^'{2,}\s*\[\[[^\]]*\]\]\s*'{2,}\s*$")

# Vadyadhyaya enumerates named drum strokes as "<mnemonic syllables> iti <name>".
# These are numbered in the source and are genuine text, but they are not
# slokas: they are tagged rather than dropped, and are barred from creating a
# prakarana division.
RE_PATAKSHARA = re.compile(r'इति\s+\S+$')
PATAKSHARA_MAX_CHARS = 70


def is_patakshara(text: str) -> bool:
    """Named drum-stroke mnemonic ("<syllables> iti <name>"), not a sloka.

    Two signatures, either of which suffices once the line already ends in
    "iti <name>": it is short, or it is anusvara-saturated. Drum mnemonics
    (khum, jhem, nakhem, dam) carry anusvara on most tokens; measured on this
    corpus, 343 genuine slokas exceed the 0.5 anusvara ratio but not one of
    them also ends in "iti <name>", so the conjunction is unambiguous.
    """
    if not RE_PATAKSHARA.search(text):
        return False
    if len(text) < PATAKSHARA_MAX_CHARS:
        return True
    toks = text.split()
    return bool(toks) and sum('ं' in t for t in toks) / len(toks) >= 0.5


def is_structural_label(text: str) -> bool:
    toks = text.split()
    if not toks:
        return True
    if len(toks) <= 2 and any(t in STRUCTURAL_LABELS for t in toks):
        return True
    return len(toks) < MIN_VERSE_TOKENS and len(text) < MIN_VERSE_CHARS


def looks_like_notation(text: str, raw: str = '') -> bool:
    """True if the line is embedded musical notation / a table row, not verse.

    Ragaviveka and Prabandha carry tab-separated prabandha grids: a row of
    svara names, then rows of song syllables padded with '०', then a bare
    numeral as the table index. None of it is verse.
    """
    if '\t' in raw:                       # tab-aligned grid column
        return True
    toks_pre = text.split()
    # "matra" only signals a grid when it is the trailing LABEL of a row
    # ("... || matra 1"). Taladhyaya's subject IS the matra, so the word
    # appears in plenty of genuine verses and must not disqualify them.
    if toks_pre and toks_pre[-1] == 'मात्रा':
        return True
    if '\u0951' in text or '\u0952' in text:   # svarita/anudatta accents
        return True
    toks = text.split()
    if not toks:
        return False

    # Taladhyaya's prastara grids are built from LONG compound svara tokens
    # ("anivipra anivisa || talika"), so the mean-token-length test cannot see
    # them. They are identified structurally instead, by two signatures:
    #   (a) a double danda together with a prastara section label
    #   (b) a standalone beat symbol token (avagraha used as a laghu/guru mark)
    # Label matching is by exact token, so a genuine verse reading
    # "...pancamavastunah" is not caught by the label "vastu".
    if '॥' in text and any(t in STRUCTURAL_LABELS for t in toks):
        return True
    if any(RE_BEAT_TOKEN.fullmatch(t) for t in toks):
        return True
    if toks.count('०') >= 1 and len(toks) <= 4:  # zero-padded grid cell
        return True
    if len(toks) < NOTATION_MIN_TOKENS:
        return False
    mean_len = sum(len(t) for t in toks) / len(toks)
    return mean_len < NOTATION_MEAN_TOKEN_LEN


def is_unmarked_boundary(nums, i) -> bool:
    """Does a reset at index i start a sustained new verse sequence?

    `nums` must already have notation rows removed -- tala matra tables run
    1,2,3,4... of their own and would otherwise forge a boundary at every grid.
    """
    exp, hits = 1, 0
    for n in nums[i:i + LOOKAHEAD]:
        if n == exp:
            hits += 1
            exp += 1
        elif exp < n <= exp + GAP_TOLERANCE:
            hits += 1
            exp = n + 1
    return hits >= MIN_RUN


# ------------------------------------------------------------------ parser
RE_HAS_PRAK = re.compile(r'प्रकरण')
# Svaragatadhyaya opens prakarana 1 with a plain (unbolded) heading line,
# "tatradimam padarthasamgrahakhyam prakaranam". Without this it is buffered
# as the first hemistich of verse 1.
RE_BARE_PRAK_HDR = re.compile(r'प्रकरणम्\s*$')


def is_bare_prakarana_line(text: str) -> bool:
    """Any unbolded prakarana marker, opening or closing. Used to route the
    line out of the verse buffer during pass 1."""
    return bool(RE_BARE_PRAK_HDR.search(text)) and len(text.split()) <= 6


def is_bare_prakarana_header(text: str) -> bool:
    """An OPENING heading. "iti ...prakaranam" closes a section and is excluded."""
    return is_bare_prakarana_line(text) and not text.startswith('इति')
RE_HAS_ADHY = re.compile(r'अध्याय')
PARISHISHTA_HDR = "'''" + 'परिशिष्ट'


def parse_chapter(title, wikitext, chap_num, chap_name):
    """Return (verses, prakaranas) for one chapter page.

    Only Svaragatadhyaya carries prakarana headers in the Wikisource text.
    Chapters 2-7 have nothing between the opening "atha <n>o <name>adhyayah"
    and the closing colophon, so every internal division there is INFERRED
    from a verse-numbering reset and marked inferred=True.
    """
    url = 'https://sa.wikisource.org/wiki/' + title.replace(' ', '_')
    raw_lines = wikitext.split('\n')

    # Pass 1 -- flatten into a stream of events, keeping source line numbers.
    events, buf = [], []
    for lineno, raw in enumerate(raw_lines, 1):
        stripped = raw.strip().lstrip(':').strip()
        if RE_TITLE_BANNER.match(stripped):
            continue
        s_ = clean(raw)
        if not s_:
            continue
        if RE_SECTION.match(stripped) or stripped.startswith(PARISHISHTA_HDR):
            if buf:
                events.append(('orphan', buf, lineno))
                buf = []
            events.append(('section', s_, lineno))
            continue
        if s_.startswith(';') or s_.startswith('=='):
            continue
        if is_bare_prakarana_line(s_):
            if buf:
                events.append(('orphan', buf, lineno))
                buf = []
            events.append(('section', s_, lineno))
            continue
        m = RE_TRAIL_NUM.search(s_)
        body_only = s_[:m.start()].strip() if m else s_

        # Notation is filtered here, at line level, BEFORE it can be buffered
        # as the first half of a verse. Otherwise a grid's bare index line
        # ("\u0966\u0967\u0966" alone on a line) swallows the real verse above it.
        if (looks_like_notation(body_only, raw)
                or (m and is_structural_label(body_only) and not buf)
                or (m and not body_only and not buf)):
            events.append(('notation', (dnum(m.group(1)) if m else -1, body_only, []), lineno))
            continue

        if m:
            events.append(('numbered', (dnum(m.group(1)), body_only, buf), lineno))
            buf = []
        else:
            buf.append(s_)
    if buf:
        events.append(('orphan', buf, len(raw_lines)))

    # Pass 2 -- pre-mark notation rows so they cannot forge a boundary.
    for k, e in enumerate(events):
        if e[0] == 'numbered':
            n, body, lead = e[1]
            events[k] = ('notation' if looks_like_notation(' '.join(lead + [body]))
                         else 'numbered', e[1], e[2])  # combined-text check

    verse_idx = [k for k, e in enumerate(events) if e[0] == 'numbered']
    nums_only = [events[k][1][0] for k in verse_idx]
    pos = {k: i for i, k in enumerate(verse_idx)}

    verses, prakaranas = [], []
    prak_num, prak_name, expected = 1, None, None
    pending_new_division = False

    def open_prakarana(name, inferred, explicit_num=None):
        nonlocal prak_num, prak_name, expected, pending_new_division
        pending_new_division = False
        if prakaranas:
            prak_num = explicit_num if explicit_num else prak_num + 1
        elif explicit_num:
            prak_num = explicit_num
        prak_name, expected = name, None
        prakaranas.append({'prakarana_num': prak_num, 'prakarana': name,
                           'inferred': inferred})

    for k, (kind, payload, lineno) in enumerate(events):

        if kind == 'section':
            txt = payload
            if RE_HAS_PRAK.search(txt):
                if txt.startswith('अथ') or is_bare_prakarana_header(txt):
                    open_prakarana(txt, False)
                else:
                    # "iti ...prakaranam" names the division that just ENDED.
                    # Attribute it backwards, then start a fresh division so the
                    # verses that follow are not filed under a closing colophon.
                    if not prakaranas:
                        prakaranas.append({'prakarana_num': prak_num,
                                           'prakarana': txt, 'inferred': False})
                    elif prakaranas[-1]['prakarana'] is None:
                        prakaranas[-1]['prakarana'] = txt
                    for vv in verses:
                        if vv['prakarana_num'] == prak_num and vv['prakarana'] is None:
                            vv['prakarana'] = txt
                    prak_name, expected = None, None
                    # Open the next division LAZILY. If an explicit "atha ..."
                    # opener follows, it supplies the name; if verses simply
                    # resume, an unnamed division is created then. Opening here
                    # would leave an empty division behind either way.
                    pending_new_division = any(
                        vv['prakarana_num'] == prak_num for vv in verses)
            elif RE_HAS_ADHY.search(txt) or txt.startswith('परिशिष्ट'):
                expected = None             # chapter open/close, not a division
            continue

        if kind == 'orphan':
            for ln in payload:
                log_review(chap_name, 'unnumbered line',
                           f'Source line ~{lineno}, prakarana {prak_num}. No verse '
                           f'number found; excluded from verses.json.', ln)
            continue

        n, body, lead = payload
        text = ' '.join(lead + [body]).strip()

        if kind == 'numbered' and pending_new_division:
            open_prakarana(None, False)

        if kind == 'notation':
            log_review(chap_name, 'embedded notation / table row',
                       f'Source line ~{lineno}, prakarana {prak_num}. Trailing '
                       f'numeral {n} is a row index in a matra/svara table, not a '
                       f'verse number. Excluded from verses.json.', text)
            continue

        note = None
        if expected is None:                # first verse of a division
            if n != 1:
                note = f'division starts at verse {n}, not 1'
                log_review(chap_name, 'division does not start at 1',
                           f'Prakarana {prak_num} opens at verse {n}. The source '
                           f'may be missing earlier verses.', text)
            expected = n
        elif n == expected:
            pass
        elif expected < n <= expected + GAP_TOLERANCE:
            note = f'numbering jumped {expected}->{n}'
            log_review(chap_name, 'numbering gap',
                       f'Prakarana {prak_num}: expected {expected}, found {n}. Verse '
                       f'kept and numbered as in the source; not renumbered.', text)
        elif n == 1 and not is_patakshara(text) and is_unmarked_boundary(nums_only, pos[k]):
            open_prakarana(f'[unmarked division {prak_num + 1}]', True)
            log_review(chap_name, 'inferred prakarana boundary',
                       f'Source line ~{lineno}: verse numbering resets to 1 with no '
                       f'section header present. Treated as the start of prakarana '
                       f'{prak_num}. VERIFY against a print edition.', text)
            expected = 1
        elif is_patakshara(text):
            # A drum-stroke entry numbered in its own local series. Keep it,
            # tagged, but do not let it move the sloka counter.
            verses.append({
                'chapter': chap_name, 'chapter_num': chap_num,
                'prakarana': prak_name, 'prakarana_num': prak_num,
                'verse_num': n, 'devanagari': text, 'content_type': 'patakshara',
                'lines': lead + ([body] if body else []), 'notation': None,
                'slp1': sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.SLP1),
                'iast': sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.IAST),
                'note': 'patakshara entry, numbered in a local series',
                'source_url': url, 'retrieved_date': RETRIEVED,
            })
            continue
        elif is_verse_substance(text):
            # The source itself repeats and skips verse numbers (Vadyadhyaya
            # numbers two different slokas 93). Dropping the text to protect a
            # counter loses real verses, so it is kept and flagged instead, and
            # is not allowed to move `expected`.
            note = f'source numbering anomaly: found {n}, expected {expected}'
            log_review(chap_name, 'numbering anomaly (verse KEPT)',
                       f'Source line ~{lineno}, prakarana {prak_num}: numbered {n} '
                       f'where {expected} was expected. The text is a genuine verse, '
                       f'so it is kept with the source number and flagged. Verify '
                       f'the numbering against a print edition.', text)
            vtext, notation = split_verse_notation(text)
            vtext = vtext.strip(' ।॥').strip()
            verses.append({
                'chapter': chap_name, 'chapter_num': chap_num,
                'prakarana': prak_name, 'prakarana_num': prak_num,
                'verse_num': n, 'devanagari': vtext, 'lines': lead + ([body] if body else []),
                'content_type': 'sloka', 'notation': notation,
                'slp1': sanscript.transliterate(vtext, sanscript.DEVANAGARI, sanscript.SLP1),
                'iast': sanscript.transliterate(vtext, sanscript.DEVANAGARI, sanscript.IAST),
                'note': note, 'source_url': url, 'retrieved_date': RETRIEVED,
            })
            continue
        else:
            log_review(chap_name, 'out-of-sequence number',
                       f'Source line ~{lineno}, prakarana {prak_num}: trailing '
                       f'numeral {n} where {expected} was expected, and it does not '
                       f'begin a new run. Likely a mis-transcribed number (e.g. a '
                       f'dropped hundreds digit). Excluded from verses.json.', text)
            continue

        if not text:
            continue
        text, notation = split_verse_notation(text)
        text = text.strip(' ।॥').strip()
        if notation:
            log_review(chap_name, 'verse split from its svara illustration',
                       f'Source line ~{lineno}, prakarana {prak_num} verse {n}: the '
                       f'line carried a sloka followed by its svara illustration. '
                       f'The verse is in devanagari/slp1/iast; the illustration is '
                       f'preserved verbatim in the "notation" field.',
                       f'VERSE: {text}\nNOTATION: {notation}')
        if len(text) > 250:
            log_review(chap_name, 'unusually long verse record',
                       f'Prakarana {prak_num} verse {n} is {len(text)} characters '
                       f'against a corpus median near 83. Probably a prose or '
                       f'notation block absorbed into the verse. Kept, but verify.',
                       text[:400])
        verses.append({
            'chapter': chap_name, 'chapter_num': chap_num,
            'prakarana': prak_name, 'prakarana_num': prak_num,
            'verse_num': n, 'devanagari': text,
            'lines': lead + ([body] if body else []),
            'content_type': 'patakshara' if is_patakshara(text) else 'sloka',
            'notation': notation,
            'slp1': sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.SLP1),
            'iast': sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.IAST),
            'note': note, 'source_url': url, 'retrieved_date': RETRIEVED,
        })
        expected = n + 1

    return verses, prakaranas


# ------------------------------------------------------------------ output
def write_chapter_md(path, chap_num, chap_name, verses):
    lines = [f'# {chap_num}. {chap_name}', '',
             f'*Sāṅgītaratnākara of Śārṅgadeva — Sanskrit Wikisource '
             f'(CC-BY-SA), retrieved {RETRIEVED}.*', '',
             f'Verses: **{len(verses)}**', '', '---', '']
    cur = None
    for v in verses:
        if v['prakarana_num'] != cur:
            cur = v['prakarana_num']
            head = v['prakarana'] or '(no heading in source)'
            lines += ['', f'## Prakaraṇa {cur} — {head}', '']
        tag = '' if v['content_type'] == 'sloka' else f" · _{v['content_type']}_"
        lines.append(f"**{v['verse_num']}**{tag}")
        lines.append('')
        lines.append('  \n'.join(v.get('lines') or [v['devanagari']]))
        lines.append('')
        lines.append(f"*{v['iast']}*")
        if v.get('notation'):
            lines.append('')
            lines.append(f"> svara illustration: `{v['notation']}`")
        if v['note']:
            lines.append('')
            lines.append(f"> ⚠ {v['note']}")
        lines.append('')
    path.write_text('\n'.join(lines))


def gap_report(verses):
    """Gaps in verse numbering within each prakarana."""
    out = []
    by = {}
    for v in verses:
        if v['content_type'] != 'sloka':
            continue     # patakshara run their own local numbering series
        by.setdefault((v['chapter_num'], v['chapter'], v['prakarana_num']), []).append(v['verse_num'])
    for (cn, cname, pn), ns in sorted(by.items()):
        ns = sorted(ns)
        missing = [x for x in range(ns[0], ns[-1] + 1) if x not in set(ns)]
        dupes = sorted({x for x in ns if ns.count(x) > 1})
        if ns[0] != 1:
            out.append(f'{cname} prak.{pn}: starts at {ns[0]}, not 1')
        if missing:
            out.append(f'{cname} prak.{pn}: missing {missing[:20]}'
                       + (' ...' if len(missing) > 20 else ''))
        if dupes:
            out.append(f'{cname} prak.{pn}: duplicate verse numbers {dupes[:20]}')
    return out


def main():
    print('Fetching (cached where possible)…')
    fetch_all()
    OUT.mkdir(exist_ok=True)

    all_verses, summary = [], []
    for idx, chap in enumerate(CHAPTER_ORDER, 1):
        title = f'{PREFIX}/{chap}'
        wt = RAW / f'{cache_slug(title)}.wikitext'
        if not wt.exists():
            print(f'  !! missing cache for {title}')
            continue
        verses, praks = parse_chapter(title, wt.read_text(), idx, chap)
        all_verses += verses
        inferred = sum(1 for p in praks if p['inferred'])
        write_chapter_md(OUT / f'{idx:02d}_{SLUGS[chap]}.md', idx, chap, verses)
        nsl = sum(1 for v in verses if v['content_type'] == 'sloka')
        summary.append((idx, chap, nsl, len(verses) - nsl, max(1, len(praks)), inferred))

    (ROOT / 'verses.json').write_text(
        json.dumps(all_verses, ensure_ascii=False, indent=1))

    gaps = gap_report(all_verses)
    hdr = ['# Review needed', '',
           f'Generated {RETRIEVED} from sa.wikisource.org.', '',
           'Every item below is something the parser could not resolve with '
           'confidence. Nothing here was guessed at or auto-corrected.', '',
           '## Verse-numbering gaps and duplicates', '']
    hdr += [f'- {g}' for g in gaps] or ['- none']
    hdr += ['', f'## Parser ambiguities ({len(review)})', '']
    (ROOT / 'review_needed.md').write_text('\n'.join(hdr + review))

    print('\n' + '=' * 74)
    print(f'{"#":>2}  {"chapter":22s} {"ślokas":>7s} {"pāṭākṣ":>7s} '
          f'{"divisions":>10s} {"inferred":>9s}')
    print('-' * 74)
    for i, c, nsl, npk, np_, ni in summary:
        print(f'{i:>2}  {c:22s} {nsl:>7d} {npk:>7d} {np_:>10d} {ni:>9d}')
    print('-' * 74)
    tsl = sum(r[2] for r in summary)
    print(f'    {"TOTAL":22s} {tsl:>7d} {sum(r[3] for r in summary):>7d}')
    print('=' * 74)
    print(f'\nNumbering gaps/duplicates flagged: {len(gaps)}')
    print(f'Parser ambiguities logged:         {len(review)}')
    print('\nSanity check vs secondary literature (approximate, NOT authoritative):')
    sv = sum(1 for v in all_verses if v['chapter_num'] == 1 and v['content_type'] == 'sloka')
    print(f'  Svaragatādhyāya: parsed {sv} vs ~170 cited  -> '
          f'{"MISMATCH — flagged, not corrected" if abs(sv-170)>20 else "ok"}')
    print(f'  Whole work:      parsed {tsl} vs ~1678 cited -> '
          f'{"MISMATCH — flagged, not corrected" if abs(tsl - 1678) > 200 else "ok"}')
    print('\nWrote verses.json, by-chapter/*.md, review_needed.md')


if __name__ == '__main__':
    main()
