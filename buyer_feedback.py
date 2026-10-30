"""Buyer feedback, by structure rather than by phrasing.

The writer never states buyer feedback. Code adds it, and only one way: a fixed,
marked block that quotes the product's Amazon customer review summary word for
word. Everything outside that block is judged by a default-deny gate.

Two halves, one vocabulary:

- find_crowd_claims: any crowd, opinion or popularity term outside the block
  is refused. The terms are a closed list (CROWD_TERMS) of nouns and
  popularity words, not a list of the verbs a claim might use, so a new
  phrasing of "owners like it" is still refused for naming owners. A false
  positive costs a rewrite. A false negative publishes invented feedback.
- the summary block: render_summary_block builds it, insert_summary_block
  places it, and find_summary_block_problems checks a block against the
  stored summary exactly. A verbatim quote cannot invent or contradict.

Pure text functions with no imports from the pipeline, so the gate, the
staging step and the published-post tests share one implementation.
"""
import html
import re
import unicodedata

# --- The stored summary -------------------------------------------------------
CUSTOMER_SUMMARY_MAX_CHARS = 900
# Characters that could open markup or a Liquid tag in the post, or hide or
# reorder text (controls, zero-width and bidi characters).
_SUMMARY_DROP = re.compile("[<>{}\x00-\x1f\x7f\u00ad\u200b-\u200f\u202a-\u202e"
                           "\u2060-\u2064\u2066-\u2069\ufeff]")


def _plain_brackets(text: str) -> str:
    """`text` with every look-alike of < > { } (fullwidth, small form) made
    plain, so the strips below see it. No other character changes."""
    return "".join(c if c.isascii() else
                   (n if (n := unicodedata.normalize("NFKC", c)) in ("<", ">", "{", "}") else c)
                   for c in text)


def clean_customer_summary(text) -> str:
    """Untrusted Amazon page text -> one plain line safe to store and quote.

    Look-alike brackets are made plain first (a fullwidth less-than sign
    becomes "<" and is then stripped), then markup and entities go, then
    every character that could open markup or a Liquid tag, or hide or
    reorder text (controls, zero-width and bidi characters). Whitespace
    collapses to single spaces. Nothing else changes: the block quotes this
    text as Amazon's, so dashes, ellipses and other punctuation stay as Amazon
    wrote them. Past the length cap, the text ends at the last sentence that
    fits, or is "" when no sentence fits, since a quote is never a cut
    sentence. Returns "" for anything that is not a non-empty string.
    Idempotent, so a stored summary cleans to itself and a quote of it stays
    exact."""
    if not isinstance(text, str):
        return ""
    s = re.sub(r"<[^>]*>", " ", _plain_brackets(text))
    s = _plain_brackets(html.unescape(s))
    s = " ".join(_SUMMARY_DROP.sub(" ", s).split())
    if len(s) > CUSTOMER_SUMMARY_MAX_CHARS:
        ends = [m.start() for m in _SENTENCE_SPLIT.finditer(s)
                if m.start() <= CUSTOMER_SUMMARY_MAX_CHARS]
        s = s[:ends[-1]] if ends else ""
    return s


# --- The summary block --------------------------------------------------------
BLOCK_START = "<!-- amazon-review-summary -->"
BLOCK_END = "<!-- /amazon-review-summary -->"
BLOCK_LEAD = "Amazon's customer review summary, read {date}:"
MAX_QUOTES = 2

# A later sentence is the balancing quote when it carries a mixed or negative
# point. "while they appreciate the flavors" is not one, so "while" counts only
# before a split ("while some", "while many").
_MIXED_CUE = re.compile(
    r"\b(?:mixed|however|disagree\w*|concern\w*|others|but|though|although)\b"
    r"|\bwhile\s+(?:some|many|most|several|a\s+few)\b"
    r"|\bsome\s+(?:customers|buyers|users|reviewers)\b", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_BLOCK_RE = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.DOTALL)
_QUOTE_LINE = re.compile(r'^> "(.+)"$')


def summary_sentences(summary: str) -> list:
    """The summary split into sentences, in order."""
    return [s.strip() for s in _SENTENCE_SPLIT.split((summary or "").strip()) if s.strip()]


def select_quotes(summary: str) -> list:
    """The sentences the block quotes: the summary's first sentence, then the
    first later sentence with a mixed or negative point, so the block is not
    all praise. One sentence when no later one qualifies."""
    sentences = summary_sentences(summary)
    if not sentences:
        return []
    balancing = next((s for s in sentences[1:] if _MIXED_CUE.search(s)), None)
    return [sentences[0]] + ([balancing] if balancing else [])


def render_summary_block(summary: str, read_on_label: str) -> str:
    """The block for `summary`, read on `read_on_label` ("Oct 29, 2026"). The
    summary is cleaned here (clean_customer_summary), so the block never quotes
    raw page text. Returns "" when there is nothing to quote."""
    quotes = select_quotes(clean_customer_summary(summary))
    if not quotes or not read_on_label:
        return ""
    quoted = "\n>\n".join(f'> "{q}"' for q in quotes)
    return (f"{BLOCK_START}\n\n{BLOCK_LEAD.format(date=read_on_label)}\n\n"
            f"{quoted}\n\n{BLOCK_END}")


# The section the block closes: the featured pick, the buying guide's top pick,
# or the single review's drawbacks. It ends at the next heading of the same or
# a higher level.
_ANCHOR_HEADING = re.compile(
    r"^(#{2,4})\s+(?:.*\bfeatured\s*pick\b|.*\btop\s+pick\b|what\s+could\s+be\s+better\b)",
    re.IGNORECASE)
_ANY_HEADING = re.compile(r"^(#{1,6})\s")
_QUESTION_H2 = re.compile(r"^##\s+(?!#).*\?\s*$")


def insert_summary_block(body: str, block: str) -> str:
    """`body` with `block` placed at the end of the featured pick's section.
    With no such section, before the first question H2, else at the end.
    An empty `block` returns `body` unchanged."""
    if not block:
        return body
    lines = body.split("\n")
    at = None
    for i, line in enumerate(lines):
        m = _ANCHOR_HEADING.match(line)
        if not m:
            continue
        level = len(m.group(1))
        at = len(lines)
        for j in range(i + 1, len(lines)):
            h = _ANY_HEADING.match(lines[j])
            if h and len(h.group(1)) <= level:
                at = j
                break
        break
    if at is None:
        at = next((i for i, line in enumerate(lines) if _QUESTION_H2.match(line)), len(lines))
    head = "\n".join(lines[:at]).strip("\n")
    tail = "\n".join(lines[at:]).strip("\n")
    return "\n\n".join(part for part in (head, block, tail) if part) + "\n"


def find_summary_block_problems(text: str, summary: str, read_on_label: str) -> list:
    """What is wrong with the summary blocks in `text`, judged against the
    product's stored `summary` (cleaned here) read on `read_on_label`. Empty
    list = no block, or exactly one block that is exactly what
    render_summary_block makes. Each check is also reported on its own, so a
    held post says why."""
    text = (text or "").replace("\r\n", "\n")
    summary = clean_customer_summary(summary)
    starts, ends = text.count(BLOCK_START), text.count(BLOCK_END)
    if starts == 0 and ends == 0:
        return []
    problems = []
    if starts != ends:
        problems.append(f"summary block markers do not pair up ({starts} start, {ends} end)")
    blocks = _BLOCK_RE.findall(text)
    if len(blocks) > 1:
        problems.append(f"{len(blocks)} summary blocks; a post carries at most one")
    expected = render_summary_block(summary, read_on_label)
    if not expected:
        problems.append("summary block with no usable stored summary behind it")
    for block in blocks:
        inner = [ln for ln in block[len(BLOCK_START):-len(BLOCK_END)].split("\n") if ln.strip()]
        lead = BLOCK_LEAD.format(date=read_on_label)
        if not inner or inner[0] != lead:
            problems.append(f"summary block does not open with {lead!r}")
        quotes, other = [], []
        for ln in inner[1:]:
            m = _QUOTE_LINE.match(ln)
            if m:
                quotes.append(m.group(1))
            elif ln.strip() != ">":
                other.append(ln)
        if other:
            problems.append(f"summary block carries other text: {other[0][:80]!r}")
        if not 1 <= len(quotes) <= MAX_QUOTES:
            problems.append(f"summary block quotes {len(quotes)} sentence(s), "
                            f"need 1 to {MAX_QUOTES}")
        for q in quotes:
            if q not in summary_sentences(summary):
                problems.append(f"quoted text is not a sentence of the stored summary: {q[:80]!r}")
        if expected and block != expected:
            problems.append("summary block differs from the one the stored summary renders")
    return problems


# --- The default-deny gate ----------------------------------------------------
# Each entry is (prompt text, pattern). The prompt text is what the prompts
# list, joined with "; ", so the stated rule and the enforced gate read from
# one place.
CROWD_TERMS = (
    # People credited with an opinion or a choice.
    ("customers", r"customers?"), ("consumers", r"consumers?"), ("buyers", r"buyers?"),
    ("owners", r"owners?"), ("shoppers", r"shoppers?"), ("purchasers", r"purchasers?"),
    ("reviewers", r"reviewers?"), ("reviews", r"reviews"),
    ("a review, one review, a top review",
     r"(?:a|an|one|another|each|every|any|this|that|the|customer|amazon|buyer|"
     r"user|star|positive|negative|glowing|rave|verified|top)\s+review\b(?!-)"),
    # A dated star rating is a listing figure. Ratings as praise are not.
    ("top-rated, highly rated, high marks, well reviewed",
     r"(?:top|highly|well|best|higher|highest)[\s-]+rated|"
     r"rated\s+(?:highly|well|best|top)|(?:high|strong|glowing|great|"
     r"excellent|stellar|solid|impressive|perfect|positive|rave)\s+"
     r"(?:star\s+)?(?:ratings?|marks)|"
     r"(?:well|highly|positively|favou?rably)[\s-]+reviewed|reviewed\s+by"),
    ("pet parents, dog moms, cat dads, pet lovers",
     r"(?:pet|dog|cat|puppy|kitten|fur)[\s-]*(?:parents?|moms?|dads?|lovers?)|pawrents"),
    ("people", r"people"), ("users", r"users"), ("folks", r"folks"),
    ("households", r"households"), ("homes", r"homes"), ("families", r"families"),
    # Professionals as a crowd ("many vets", "trainers swear by") or as an
    # endorsement ("vet-approved", "veterinarian-recommended"). "Ask your vet"
    # and "a trip to the vet" are advice and errands, and stay.
    ("vets, veterinarians, vet-approved, veterinarian-recommended",
     r"vets|veterinarians|(?:vet|veterinarian|veterinary)[\s-]+(?:recommended|approved|"
     r"endorsed|trusted|favou?rite|pick|choice|formulated|developed|designed)"),
    ("trainers", r"trainers"), ("groomers", r"groomers"), ("behaviorists", r"behaviou?rists"),
    ("everyone, anyone, no one",
     r"everyone|everybody|anyone|anybody|nobody|no\s+one(?![\s-]size)"),
    ("feedback", r"feedback"), ("testimonials", r"testimonials?"),
    ("success stories, anecdotes",
     r"success\s+stories|stories\s+(?:abound|from|about)|anecdot\w*"),
    ("complaints, praise, gripes, criticism, compliments",
     r"complain\w*|complaints?|prais(?:e|es|ed|ing)|gripes?|criticism|compliments?"),
    # Popularity and sentiment.
    ("popular", r"popular\w*"), ("favorite (your pet's favorite is fine)", r"favou?rites?|faves?"),
    ("best-selling, top-selling", r"best[\s-]*sell\w*|top[\s-]*sell\w*"),
    ("beloved", r"beloved"), ("reputation", r"reputation\w*"), ("go-to", r"go[\s-]to"),
    ("thumbs-up", r"thumbs[\s-]*up"), ("consensus", r"consensus"),
    ("verdict (our verdict is fine)", r"verdicts?"),
    ("raves", r"rav(?:e|es|ed|ing)"), ("swear by", r"(?:swear\w*|swore|sworn)\s+by"),
    ("reach for", r"reach(?:es|ed|ing)?\s+for"),
    ("snap up", r"snap(?:s|ped|ping)?\s+(?:\w+\s+)?up"),
    ("repeat purchases, reorders", r"repeat\s+(?:purchase\w*|buy\w*|customers?|orders?)|"
                                   r"re-?(?:order|buy|purchas)\w*"),
    ("word of mouth", r"word\s+of\s+mouth"), ("vocal about", r"vocal\s+about"),
    ("most picked, most bought, chosen most often",
     r"(?:chosen|picked|bought|ordered|purchased|preferred)\s+"
     r"(?:more|most)\s+(?:often|frequently)|most[\s-]+(?:picked|"
     r"chosen|bought|ordered|purchased|loved|trusted|recommended)"),
    ("fans", r"fans?(?!-shaped)|fanbase"),
    ("cult following, loyal following",
     r"cult|(?:loyal|devoted|big|huge|strong|dedicated)\s+following"),
    ("crowd-pleaser", r"crowd[\s-]*pleas\w*"), ("household name", r"household\s+name"),
    ("loved by, trusted by, recommended by",
     r"(?:loved|trusted|adored|praised|recommended|endorsed|approved|preferred|chosen)\s+by"),
    ("well loved, widely liked, highly recommended",
     r"(?:well|much|widely|universally|dearly)[\s-]+(?:loved|liked|regarded|"
     r"known|recommended)|highly\s+recommended|comes\s+recommended"),
    ("reportedly, by all accounts, they say",
     r"reportedly|by\s+all\s+accounts|they\s+say|it\s+is\s+said"),
    ("wins over, a hit with", r"wins?\s+over|won\s+over|hit\s+with"),
    # A bare quantifier as the speaker: "Most agree", "Others find it too loud".
    ("many say, most agree, others find",
     r"(?:many|most|some|several|few|others|plenty|lots)\s+"
     r"(?:say|says|said|agree\w*|report\w*|mention\w*|note[sd]?|find|"
     r"found|feel|felt|claim\w*|swear\w*|swore|rave\w*|prais\w*|"
     r"love[sd]?|like[sd]?|prefer\w*|recommend\w*|describ\w*|"
     r"complain\w*|call|calls|called|consider\w*|think|thinks|thought|"
     r"insist\w*|reach\w*|buy|buys|bought|choose|chooses|chose|pick|"
     r"picks|picked|wish\w*|warn\w*|point(?:s|ed|ing)?\s+out)"),
)
_TERM_RE = re.compile(r"\b(?:" + "|".join(f"(?:{p})" for _, p in CROWD_TERMS) + r")\b",
                      re.IGNORECASE)

# Product names that carry a term and appear in published posts. The
# published-post test has no products.json entry to read names from (an entry
# is retired once its post publishes, and older posts carry no listing_title),
# so the names it must read as names are listed here. The entry's own names
# are added per call.
PRODUCT_NAMES = ("MidWest Homes for Pets",)

# Spans that are not a crowd claim. Where a pattern has a group named m, only
# that group is blanked, so "your dog's favorite" keeps the rest in view.
_PET = r"(?:dog|cat|pet|puppy|kitten)"
_EXEMPT = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in (
    r"\]\([^)\s]*\)", r"https?://\S+",                               # link targets, URLs
    r"happy\s*pet\s*product\s*reviews", r"happypetproductreviews",   # the site's name
    # The verified review count: "12,000 Amazon reviews", "3,412 customer reviews".
    r"\d[\d,.]*\s+(?P<m>(?:(?:amazon|customer|verified)\s+)*reviews)\b",
    r"(?P<m>review)\s+count\s+(?:of\s+)?\d",
    r"\bour\s+(?P<m>verdict)\b", r"^#{1,6}\s+(?:the\s+)?(?P<m>verdict)\b",
    # Research, not shoppers: "a 2022 review of osteoarthritis trials".
    r"\b(?:research|systematic|scientific|literature)\s+(?P<m>reviews?)\b",
    r"\b(?P<m>reviews?)\s+of\s+(?:[\w-]+\s+){0,6}?(?:trials|studies|research|evidence|literature)\b",
    # Training advice: "praise your dog", "a treat and praise".
    r"\b(?P<m>praise)\s+(?:your|him|her|them)\b",
    r"\b(?:verbal|lots\s+of|plenty\s+of|with|and|or)\s+(?P<m>praise)\b",
    r"\b(?P<m>owner'?s?)\s+manual\b", r"\b(?P<m>customer)\s+(?:service|support|care)\b",
    r"\b(?P<m>buyer'?s)\s+guide\b",
    # A pet's own favorite, never a crowd's: "your dog's favorite", "its
    # favorite spot". "America's favorite" and "Amazon's favorite" stay refused.
    rf"\b(?:your|their|its|(?:your|the)\s+{_PET}(?:'s|s'))\s+(?:(?:very|all-time)\s+)?"
    r"(?P<m>favou?rite)\b",
    r"\b(?:cooling|ceiling|box|desk|electric|exhaust|oscillating)\s+(?P<m>fans?)\b",
)]
_DROP = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff*_]")
_DASHES = re.compile("[\u2010-\u2015\u2212]")
_BOUNDARY = re.compile(r"[.!?](?=\s|$)|\n\s*\n|\n(?=\s*(?:#|\||>|[-+]\s|\d+[.)]\s))")
# The sourced mask. A crowd term the featured product's own listing title
# carries ("Vet Recommended") may be repeated when its sentence credits the
# listing before the term: "the listing calls it vet recommended".
_LISTING_CREDIT = re.compile(
    r"\b(?:(?:the|its|this|amazon's|the\s+amazon)\s+(?:product\s+)?listing(?:\s+title)?|"
    r"listed\s+as)\b", re.IGNORECASE)
_WORD = re.compile(r"[\w']+")


def normalize(text) -> str:
    """NFKC, invisible characters and markdown emphasis removed, curly
    apostrophes and dash variants made plain, so "**Owners**", a fullwidth "Owners"
    and one split by a zero-width space all read "Owners"."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _DROP.sub("", t.replace("\r\n", "\n"))
    return _DASHES.sub("-", t.replace("\u2019", "'").replace("\u2018", "'"))


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]


def _sentence_start(text: str, start: int) -> int:
    return max((m.end() for m in _BOUNDARY.finditer(text, 0, start)), default=0)


def _snippet(text: str, start: int, end: int) -> str:
    left = _sentence_start(text, start)
    right = next((m.start() + 1 for m in _BOUNDARY.finditer(text, end)), len(text))
    return " ".join(text[left:right].split())[:140]


def _words(text: str) -> list:
    """Lower-cased words, so case, spacing and hyphens never decide a match."""
    return _WORD.findall(normalize(text).lower())


def _is_sourced(text: str, m, listing_words: list) -> bool:
    """True when term match `m` repeats the listing title's own words, in
    order and whole, and its sentence credits the listing before it."""
    term = _words(m.group(0))
    n = len(term)
    if not any(listing_words[i:i + n] == term for i in range(len(listing_words) - n + 1)):
        return False
    return bool(_LISTING_CREDIT.search(text, _sentence_start(text, m.start()), m.start()))


def find_crowd_claims(text: str, names=(), allow_block: bool = False,
                      listing: str = "") -> list:
    """Every crowd, opinion or popularity term in `text`, as '"term": sentence'
    (deduped by sentence, in order). Empty list = clean.

    `names` are the entry's product names, read as names and not as words.
    `listing` is the featured product's stored listing title. A term the
    title carries word for word is allowed where its sentence credits the
    listing first ("the listing calls it vet recommended"). The same words
    with no credit, or under another product's title, are refused.
    `allow_block` exempts well-formed summary blocks; whether a block is right
    is find_summary_block_problems' job. Without it, a block is just more
    text, and its lead names a customer review summary, so the writer cannot
    forge one."""
    t = normalize(text)
    masked = t
    if allow_block:
        for m in _BLOCK_RE.finditer(masked):
            masked = _blank(masked, *m.span())
    for rx in _EXEMPT:
        for m in rx.finditer(masked):
            span = m.span("m") if "m" in rx.groupindex and m.group("m") else m.span()
            masked = _blank(masked, *span)
    for name in tuple(PRODUCT_NAMES) + tuple(n for n in names if n and n.strip()):
        words = normalize(name).split()
        rx = re.compile(r"\b" + r"\s+".join(map(re.escape, words)) + r"(?!\w)", re.IGNORECASE)
        for m in rx.finditer(masked):
            masked = _blank(masked, *m.span())
    listing_words = _words(listing)
    found, seen = [], set()
    for m in _TERM_RE.finditer(masked):
        if listing_words and _is_sourced(masked, m, listing_words):
            continue
        snippet = _snippet(t, m.start(), m.end())
        if snippet.lower() in seen:
            continue
        seen.add(snippet.lower())
        found.append(f'"{" ".join(m.group(0).split())}": {snippet}')
    return found


def prompt_term_list() -> str:
    """The refused terms as the prompts state them, one group per "; "."""
    return "; ".join(text for text, _ in CROWD_TERMS)
