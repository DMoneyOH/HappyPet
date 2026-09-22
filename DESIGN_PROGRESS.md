# HappyPet — visual/UX redesign, working notes

> **Round 2 is the current state of the branch.** Everything from `## 0` down to the end
> of the first progress log describes ROUND 1, which the Director rejected on 2026-09-14:
> he liked the recovered product thumbnails and the latest-review hero, but the site as a
> whole "still reads too much like the old one" and he wanted something "entirely
> different, modern." Round 1's §5 (design direction) and §6 are therefore **superseded** —
> read them as history, not as the spec. Round 2 starts at "## Round 2" near the bottom.
> Round 1's §0 (hard constraints), §2 (the thumbnail unlock), §3 (ruled-out card data) and
> §4 (the preview approach) all still hold and were built on, not replaced.


Owner: Scarlett (Maeve fleet). Dispatched by Maeve-main on the Director's direct ask,
2026-09-14. This file is the durable state for a multi-dispatch job — keep it current
*as you go*, not at the stopping point.

**Status: IN PROGRESS.** Nothing has shipped. Nothing has been pushed. Work lives on the
branch `design/2026-09-visual-refresh`.

---

## 0. Hard constraints (read before touching anything)

- **Never push.** `main` has an automated consumer: `publish.yml` fires on cron and on a
  push of `_posts/DRAFT-*.md`, then dispatches a live GitHub Pages deploy with no human
  gate. Per the repo's own `README.md` a push here is **Class 3** and needs the Director's
  explicit go-ahead, routed through Maeve-main. Work on a branch, commit locally at most.
- **Do not touch `_posts/`.** The publishing pipeline owns it.
- **No Ruby, Bundler, Jekyll or `gem` on this machine** (checked 2026-09-14). The site
  cannot be built locally. Do not try to install a toolchain for this. See §4 for the
  preview approach that replaces it.
- **No open-web access.** Preview renders run in a Chromium with no network and no
  JavaScript (`mcp__html-render__render_html_page`).

## 1. Why the last two passes were rejected

Two prior passes (2026-09-09, previews at `80-workspace\scarlett\pages\hppr-home-v1/v2.html`,
never committed to this repo) only rearranged colours and geometric shapes. The Director's
words: "highly modern, highly user-friendly, very appealing to the eye to pet users."

Looking at the v2 render, two concrete faults:

1. **Every image was a grey placeholder paw.** The page was a wall of empty boxes. The
   product photography already existed on disk and went unused.
2. **The card told a shopper nothing they came for.** Category, species, title, a truncated
   prose excerpt, and a date. That is a blog index. Visitors arrive from Pinterest asking
   "which one should I buy" — the date is close to worthless to them.

So the bar for "not another surface pass" is: **the page must change what information the
user gets**, not just how it is coloured.

## 2. The unlock — 49/49 posts can have a real product photo

`assets/images/pins/*.jpg` holds 49 Pinterest pins (1000x1500), each with a real product
photo composited into a stage band at the top. `generate_pin_images.py` draws that band at
a fixed `IMG_Y = 90` with a *variable* `IMG_H`, and pads/centres the product by 30px — so
the `x = 0..29` column stays pure `stage_bg` inside the band and runs out exactly at the
band's bottom edge. That makes the band detectable, and the product bounding box inside it
croppable.

**Audited 2026-09-14 over all 49 posts: 49 usable, 0 empty.** Product coverage of the band
ranges 0.22–0.96. `stage_bg` is *not* always white (`get_stage_bg` samples the photo) —
sample it, never assume. So the redesign can be image-led as a primary state, not as a
best case with a placeholder fallback.

Pin resolution per post: frontmatter `pin_image` first (strip the `?v=` cache-buster), then
a slug guess off the post filename, preferring `<slug>-v2.jpg` over `<slug>.jpg`. Only 23 of
49 posts carry `pin_image`; the slug fallback covers the other 26. Both paths were exercised
in the audit.

## 3. Things checked and ruled OUT (don't re-litigate)

- **No prices or star ratings on cards.** The body prose has them, which is tempting.
  Amazon Associates restricts displaying price data sourced outside PA-API, and this repo's
  own `CLAUDE.md` carries "Amazon scraping → PA-API keys" as a standing lesson. A stale
  price on an affiliate card is a compliance problem, not a design win.
- **No "Top pick: <product>" on cards.** Only 10 of 49 posts contain a "Featured pick" line.
- **No "N picks" chip.** `###` heading counts per post range 0–15. Not a stable attribute.
- **No Worm research requested.** The bottleneck here is internal — unused assets on disk
  and an index that withholds shopper-relevant information — not a gap in knowing what
  modern design looks like. Flagged to Maeve-main rather than silently omitted.

## 4. Preview approach (replaces a local Jekyll build)

Hand-maintaining a parallel HTML mock is exactly how the last pass ended up previewing
placeholder paws where production would have had photos: the mock drifted from the thing it
stood in for. Instead, `scripts/preview_render.py` **derives the preview from the real
`_layouts/` and `_includes/` files** — a deliberately tiny Liquid subset covering only what
those layouts use (`{{ page.x }}`, `{{ site.x }}`, `{% for %}`, `{% if/elsif/else %}`,
`{% include %}`, `{% assign %}`, and the handful of filters in play). It inlines the CSS,
base64s the fonts and images, and writes into `80-workspace\scarlett\pages\`.

It is **not** a Liquid engine and must not grow into one. If it starts fighting the layouts,
fall back to a hand-written mock and carry the drift risk explicitly in the return.

Known preview-vs-production divergences to keep in mind when judging a render:
- **No JavaScript.** Anything built by script is absent from the image.
- **No network.** All fonts and images must be self-hosted or base64'd.

## 5. Design direction (decided 2026-09-14)

**Type — self-hosted Fredoka Bold for display, system stack for everything else.**
Production currently pulls 9 weights across two families from Google Fonts. Both TTFs are
already in `assets/fonts/`, and only the Bold weights exist there. Registering Nunito at 700
only and letting body text fall back would mean judging layout against the wrong metrics in
every preview. So: self-host `Fredoka-Bold.ttf` (display only, the one weight display ever
needs), drop the Google Fonts stylesheet and both preconnects entirely, and run body/UI on a
system stack. Preview fidelity becomes exact — same machine, same Chromium, same fonts — and
production loses a render-blocking external stylesheet, which is a real LCP win on a site
whose traffic is overwhelmingly mobile Pinterest. Brand identity carries on Fredoka display
plus colour plus the photography, which is what actually matches the pins.

**Colour — 7 hues down to 3 plus neutrals.** The current palette runs peach, apricot, coral,
teal, sun, mint and lavender, which is what makes it read as a kids' template. Keep the two
that are real brand equity and appear in every pin — teal `#0D5C63` and coral `#FF6B4A` —
plus sun `#FFD166` as a tertiary highlight only. Retire mint, lavender, apricot and
peach-mid. Surfaces move from pink-tinted peach to a warm neutral paper, so the product
photography carries the colour instead of competing with the chrome.

**Layout — the hero becomes a featured review.** The current hero spends the whole of a
phone's first screen on an illustration and two randomly-chosen JS cards, then hides the
illustration entirely under 520px and swaps in a separate image strip. Replace it with the
newest review presented editorially: real product photo, category, title, benefit line, CTA.
It is immediately useful, it uses the asset unlocked in §2, and it puts a real product card
in reach within one scroll on a 390px screen.

**Design mobile-first.** Render at 390px before anything else. Pinterest is the traffic
channel and that traffic is overwhelmingly mobile; the existing CSS is desktop-first with
mobile patched in (hero graphic hidden under 520px, nav search hidden under 768px).

## 6. Structural fixes folded into the redesign

- **Extract `_includes/post-card.html`.** The card markup is currently duplicated across
  `_layouts/home.html`, `dogs.md` and `cats.md`, so a redesign would otherwise mean
  redesigning the card three times and watching them diverge on the next change.
- **Move the hero cards out of JavaScript.** `_includes/hero-graphic.html` builds them in a
  script, so they are absent for any crawler or no-JS visitor. That is a production defect,
  not only a preview artifact.

---

## Progress log

- **2026-09-14 13:08 ET** — Dispatched. Read `CLAUDE.md`, `README.md`, `_config.yml`, all
  three layouts, `_includes/hero-graphic.html`, `assets/css/style.css`, `dogs.md`, and a
  representative post.
- **2026-09-14 ~13:2x ET** — Confirmed no Ruby/Jekyll toolchain. Pulled the two rejected
  previews and read the v2 render; diagnosis in §1.
- **2026-09-14 ~13:3x ET** — Audited pin-thumbnail extraction across all 49 posts: 49/49
  usable (§2). Ruled out price/rating/top-pick/pick-count card data (§3). Settled type and
  colour direction (§5).

- **2026-09-14 ~14:0x ET** — Built `scripts/make_thumbs.py` (49/49 thumbnails into
  `assets/images/thumbs/`) and `scripts/preview_render.py` (the layout-derived preview,
  now with include parameters, `site.static_files`, a `page` mode, and a `--dark` flag
  that promotes the dark block so the scheme can actually be seen).
- **2026-09-14 ~14:3x ET** — Rewrote `assets/css/style.css`, `_layouts/default.html`,
  `_layouts/home.html`, `_layouts/post.html`; added `_includes/post-card.html`; converted
  `dogs.md`, `cats.md`, `about.md`, `contact.md`, `privacy-policy.md`, `search.md` onto the
  system. Rendered and inspected each at 390px and 1280px, light and dark.

### What has actually been rendered and looked at
Home at 390 light, 390 dark, 768 and 1280. An article at 390 light and 390 dark.
`/dogs/`, `/cats/`, `/about/`, `/contact/`, `/search/` and `/privacy-policy/` at 390.
Every page of the site has now been rendered and inspected in at least one scheme.

**Not covered:** dark mode on anything but the home page and one article; any width
below 390px; and a real Jekyll build, which this machine cannot run at all (§0).

### Defects found by looking at renders (each fixed)
1. The hero headline was **invisible**. A `rise` keyframe opened at `opacity: 0` with
   `animation-fill-mode: both`, so the text was hidden until the animation ran. The entry
   animation is gone; nothing on the page is now hidden waiting for motion.
2. **`[hidden]` did nothing.** The filter rail and "show more" both work by toggling the
   attribute, and a class-level `display` beats the user agent's `[hidden] { display: none }`.
   Both controls would have run and moved nothing. Fixed with a global `[hidden]` rule.
3. **The dark-mode category chip measured 1.27:1** and was effectively invisible: the light
   scheme's chip ink is dark teal on a pale teal tint, and reused in dark it became dark teal
   on dark teal. Split out as `--chip-ink`, 6.68:1 light and 7.01:1 dark.
4. The featured review **printed twice**, as the hero and again as the first grid card.
5. Two chips per card **wrapped to a second row** on a 2-up phone grid and knocked
   neighbouring titles out of alignment. Cards now carry the category only.
6. The sticky buy bar **covered the purchase panel's own buy links**, because anchored to
   the article it sticks from the very top. It now lives in a wrapper that begins below the panel.
7. Three hero buttons **wrapped to a third line** at 390px. One call to action now.
8. The comparison table forced horizontal scroll on a phone. It wraps and fits instead.
9. The `<noscript>` navigation fallback, added to fix a real gap, **restacked the desktop
   nav into a column and turned the header into a 270px wall**, because the rule was not
   scoped to the widths where the drawer exists. The media query is load-bearing.
10. The open drawer **overlapped the wordmark**: it is a flex item in the header row and had
   no way to wrap onto its own line. This hit the JavaScript drawer too, not only no-JS.
11. The standfirst ran straight into the first prose paragraph, because `.prose > * + *`
   cannot put space above a first child.
12. The search page was an autofocused box over a blank screen, and with the script absent it
   was a dead end. It now carries a category empty state that doubles as the no-JS fallback.

### Accessibility finding worth stating plainly
The **previous accent `#FF6B4A` carried white button labels at 2.82:1**, which fails WCAG AA
for both body and large text. Every primary button, buy button and nav CTA on the live site
failed. The accent is now `#C93E1B` (5.01:1 against white). The bright coral survives only in
dark mode, where it sits on a dark ground at 7.65:1. Every pair in the palette was computed,
not eyeballed.

### Next actions (for a fresh dispatch picking this up)

Everything runs from the repo root with the project venv:

    ./.venv/Scripts/python.exe scripts/make_thumbs.py            # regenerate thumbnails
    ./.venv/Scripts/python.exe scripts/preview_render.py home --name hppr-home
    ./.venv/Scripts/python.exe scripts/preview_render.py post --slug best-dog-pools
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug about
    ./.venv/Scripts/python.exe scripts/preview_render.py home --dark --name hppr-home-dark

then render the written page by bare name with `mcp__html-render__render_html_page` and
**open the PNG and look at it**. Every defect in the list above was found that way and none
of them were visible in the markup.

Still open:
1. `_includes/hero-graphic.html` is orphaned. `git rm` is blocked by a fleet hook on this
   machine, so it is still on disk; delete it in a pass that can.
2. No 404 page exists.
3. Post front matter written by `generate_posts.py` contains em-dashes, which now show on
   card faces through `post.description`. `_posts/` is out of scope for this work, so this
   is a note for whoever owns the generator, not something changed here.
4. **Future posts get no thumbnail.** `publish.yml` runs `generate_pin_images.py` on a new
   post; nothing runs `make_thumbs.py`. Every review published from now on falls back to
   `post.image`, an Amazon CDN hotlink, so the catalogue degrades one card at a time.
   Nothing breaks, because the fallback is designed, but the fix is a change to the
   publishing pipeline and that is not a design decision to make unilaterally. Either wire
   the thumbnail step into the pipeline or accept the fallback.
5. **Unrelated to this work, worth someone's attention:** `CLAUDE.md`, `HANDOFF.md` and the
   four `HANDOFF-archive-*.md` files are not in `_config.yml`'s `exclude:` list and have no
   front matter, so Jekyll static-copies them to the live site. Internal notes are readable
   at `happypetproductreviews.com/HANDOFF.md`. `DESIGN_PROGRESS.md` had the same problem and
   was added to `exclude:`; the others were left alone because they predate this work.
6. Nothing is committed to `main` and nothing is pushed. A push here is Class 3 (see §0).

---

## Round 2 — "The Index" (2026-09-14, second structural pass)

Round 1 changed the paint. This changes the skeleton. The diagnosis, from looking at
round 1's own 1280px render rather than at the markup: teal header bar, rounded pill
nav, hero split text-left/card-right, a pill filter rail, and a uniform 4-up grid of
white rounded shadowed cards with pastel chips, all set in rounded Fredoka. Every one of
those is a 2019 affiliate-blog convention. No recolour reaches that; the shapes had to go.

### R2.1 What actually changed, structurally

| Round 1 | Round 2 |
| --- | --- |
| Teal header bar, pill nav, drawer under 1000px | Editorial masthead on paper: an ink strip with a numeric line, then a sticky hairline row. Four always-visible links, **no drawer and no nav script** |
| Hero = headline left, one featured-review card right | Hero = a typographic statement ("which one should you **actually buy?**") plus a bleeding strip of recent product shots. No card |
| One flat rail of 10 topic pills | **Two live axes**: species (permanent) x use-case (Feeding/Play/Beds/Walking/Care/Travel/Tech) |
| Uniform grid of rounded, shadowed cards | **A numbered editorial index.** No card box anywhere: photo, running numeral, small-caps label, title, one line, hairline rule. Scale varies — 1 major, 2 wide, then three-up |
| Nothing below the grid | A near-black **"how we choose"** band, then a heavy editorial footer with a full category index |
| Fredoka Bold webfont, 7 hues to 3 + neutrals, 16px radii, tinted shadows | **No webfont at all.** System grotesque for display, Georgia for prose. Ink on bone paper plus **one** accent. **Radius 0 everywhere**, one shadow token (the sticky buy bar only) |

### R2.2 The three locked decisions, and why

**Type: no webfont, system grotesque for display, Georgia for prose.** A high-contrast
editorial serif was the first instinct and it was wrong: on a web-safe stack it means
Georgia on Windows/iOS and **Noto Serif on Android**, a low-contrast humanist face that
looks nothing like what gets designed here — and every preview on this machine renders
the good case, which is exactly the drift `preview_render.py` exists to prevent. A serif's
contrast *is* its personality, so it is the worst thing to leave to a fallback. A heavy
grotesque at 48px+ reads the same on SF Pro, Roboto and Segoe UI. Display identity now
rides on **scale, tracking and the lime**, all of which are fully controlled. Georgia
survives for long prose only, where cross-platform variance is harmless.
Dropping Fredoka also removes a 48KB render-blocking download. **Not seen, and stated as
such:** the Android and iOS renderings. The file stays in `assets/fonts/` — the pin
generator still uses it, and the Pinterest pins keep the rounded face, so the site and
the pins no longer share a display face. That is a deliberate trade, not an oversight.

**Colour: ink, bone, and one accent used as a SURFACE.** `--accent` (`#D8F24B`) is never
ink on paper — it fails AA at any size there. It only ever appears as a filled block with
ink on it at 14.5:1: the highlighted phrase in the headline, the lead entry's numeral,
the primary button, the buy button, the numbers in the dark band. Ink on paper is 15.6:1,
`--ink-2` 7.9:1, `--ink-3` 4.7:1, paper on night 16.5:1. Every pair computed, not eyeballed.

**Species stays primary.** Use-case nav (the Wild One pattern) is a good second axis but a
bad replacement: dog-vs-cat is the strongest filter this audience has, `/dogs/` and
`/cats/` are real routes, and swapping species out would orphan them. Both axes run at
once, and the counts on the species row (31 dogs, 21 cats) come from the post list.

### R2.3 The trust band is written to what is actually true

The RTINGS pattern is numeric trust markers up top — but RTINGS *buys and tests* products
and HappyPet does not. A "40,000 sq ft testing facility"-shaped claim here would be a
false-advertising exposure, so the band is titled **"how we choose"**, not "how we test",
and it carries only what is verifiable or already the site's own standing claim: the post
count, zero sponsored posts, three retailers linked, and four points restated from the
existing `about.md` and the footer disclosure. Nothing new was invented about process.

### R2.4 Defects found by rendering and looking (each fixed)

1. **The lime highlight painted over the line above it** and ate the descenders of
   "should you". An inline element's background box is the font's content area (~1.17em)
   while the line box was 0.92em. The highlighted phrase is now a `display:block` with its
   own padding, which pushes instead of overlapping and keeps the leading tight.
2. **The lead entry's `01` block stretched the full column width** — `display:inline-block`
   inside a grid still stretches without `justify-self: start`.
3. **At 1280 the headline was too big for its own column.** A 7.4rem cap meant
   "actually buy?" could not fit one line, so the `fit-content` block clamped to the full
   column and left a slab of lime hanging past the "?". Capped at 5.4rem.
4. **The desktop hero had a dead top-right quadrant.** The aside was bottom-anchored only;
   it is now a full-height flex column with a small fact list at the top (desktop only —
   on a phone it would push the index two scrolls down for facts the band already states).
5. **The lead entry's text column did not line up with entry 03's.** It used its own
   1.25fr/1fr split; now two equal halves on the same 2.2rem column gap as the grid.
6. **At 320px the nav clipped "Search"** and **"the index" collided with its count.**
   A `max-width: 379px` block shrinks the nav and lets the section heads wrap. Nothing in
   it applies at 390 and up.
7. The autofocused search box drew a floating rectangle on a page with no rectangles;
   the focus ring now hugs the field (`outline-offset: 0`) rather than being removed.
8. **The sticky masthead did not stick, and no render on this machine could have shown
   it.** `position: sticky` was on `.mast-row`, whose parent `<header class="mast">` was
   only as tall as its own two rows — a sticky element is constrained to its parent's
   box, so the row would have unpinned after roughly 28px of scroll. A pinned header was
   designed and an ordinary one would have shipped. The strip now sits outside the
   header and `position: sticky` is on `.mast` itself, whose parent is the body. The
   contrast worth remembering: `.buybar`'s `sticky; bottom: 0` was always correct,
   because its parent `.reading` is article-height. Same property, opposite outcome,
   decided entirely by the parent. Caught by review, not by looking — nothing scrolls
   in a static image.
9. **The wide variant's photo was smaller than a standard entry's**, inverting the one
   hierarchy the index depends on. `3/2` on a full-width mobile entry is *shorter* than
   the standard `4/3`, and at 560-899px the more specific `3/2` beat the `1/1` column
   rule, giving entries 02-03 a 200x133 tile next to 04+'s 200x200. Now `5/4` on mobile,
   `1/1` at tablet (matching, with the title bump carrying the step up) and `16/10` only
   at 900px, where the wide column is genuinely twice as wide.

### R2.5 What was rendered and actually looked at

Home at 320, 390 (viewport and full), 1280 (viewport and full) light, and 390 dark
including the dark band and footer. An article at 390 light, 390 dark and 1280.
`/dogs/` at 768. `/about/` and `/search/` at 390. Crops were taken at native resolution
rather than judging a downsampled full-page image.

**Not covered, stated rather than implied:** dark mode on `/dogs/`, `/cats/`
and the static pages; any real browser other than this machine's Chromium; iOS and
Android type rendering; and a real Jekyll build, which this machine still cannot run.
The renderer has no JavaScript, so the two-axis filter and "show more" were reviewed as
code, never seen working. **Read every render above with this in mind: the script's
`apply()` runs on load and leaves 12 entries visible, so a real browser shows 12 and a
paging button where these images show all 49 in one unbroken column.** The tall render
is not the shipped page. The global `[hidden] { display: none !important }` rule that
makes both controls work is still in the resets — `.entry` is `display: grid`, which
would otherwise beat the user agent's own `[hidden]` rule, exactly as it did in round 1.

### R2.6 Carried forward, still open

1. `_includes/hero-graphic.html` is still orphaned and still on disk. `rm` is refused by
   a fleet hook on this machine (rm-cwd) and routing around it with an interpreter is not
   something to do quietly. Delete it from a session that can.
2. No 404 page exists.
3. Em-dashes in `_posts` front matter still reach entry faces through `post.description`.
   `_posts/` is out of scope; this is for whoever owns `generate_posts.py`.
4. **Future posts still get no thumbnail.** `publish.yml` runs `generate_pin_images.py`
   but nothing runs `make_thumbs.py`, so every new review falls back to `post.image`, an
   Amazon CDN hotlink. The index is image-led, so this degrades the page one entry at a
   time. Wiring the thumbnail step into the pipeline is a pipeline change, not a design
   decision to take unilaterally.
5. `CLAUDE.md`, `HANDOFF.md` and the four `HANDOFF-archive-*.md` files are still absent
   from `_config.yml`'s `exclude:` and are still static-copied to the live site.
6. `scripts/preview_render.py` gained a `--limit N` flag. Preview only: 49 entries make
   the home page ~26,000px tall, past the renderer's 16,000px clip, so the sections below
   the index could not otherwise be seen at all.
7. Nothing is committed to `main` and nothing is pushed. A push here is Class 3 (see 0).

### R2.7 Commands

    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r2-home
    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r2-short --limit 10
    ./.venv/Scripts/python.exe scripts/preview_render.py home --dark --limit 5 --name r2-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py post --slug best-dog-pools --name r2-post
    ./.venv/Scripts/python.exe scripts/preview_render.py dogs --name r2-dogs
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug about --name r2-about

then render by bare name with `mcp__html-render__render_html_page` and **open the PNG and
look at it.** Every defect in R2.4 was found that way; none were visible in the markup.

---

## Round 3 — palette and the top-of-page pet signal (2026-09-22)

Round 2 was reviewed live by the Director. **He kept the direction**: the structure and the
modernness stay, and nothing goes back toward round 1 or the old skeleton. Two things were
wrong, both about identity rather than layout:

1. **Black plus acid lime reads "techie", not "pet".** He wants warm and colourful.
2. **Nothing says "pet site" at a glance.** The old site had dog-and-cat art at the top doing
   that job in the first half-second. Round 2 has no equivalent.

This round changes those two things and touches nothing else. Every round 2 lock except
colour survives intact: radius 0 everywhere, one shadow token, hairlines instead of cards,
1-major / 2-wide / rest-standard scale, no webfont, system grotesque display, Georgia prose,
two live filter axes with species permanent.

### R3.1 The colour rule that replaced round 2's

Round 2's rule was "ink on paper plus one accent used as a surface". The failure was not the
particular hue. It was that the only large flat areas on the page were near-black, which is
what a tech product looks like. So the new rule keeps the surface-only discipline and gives
it warmth and quantity. It is written into the stylesheet header as lock 2:

| Token | Hue | Job | Contrast |
| --- | --- | --- | --- |
| `--accent` | marigold `#FFB627` | **Surface only.** Headline phrase, hero pet panel, lead numeral, every primary and buy button | ink on it 9.6:1 |
| `--hot` | tomato `#C6381B` | **Ink only.** Running numerals, live filter state | 4.8:1 on paper |
| `--night` | spruce `#16402F` | The one dark section ground, replacing near-black | cream on it 10.6:1 |
| `--paper` | cream `#FFF3E2` | Page ground | ink 15.4:1, `--ink-2` 8.1:1, `--ink-3` 5.0:1 |

Saturated hue appears as **area**, never as a pastel tint and never as a coloured chip on a
card. That distinction is doing real work: seven pastel hues on small chips is exactly what
made round 1 read as a kids' template, and it is not the same thing as three saturated hues on
large flat blocks. One accent, one ink hue, one band ground, used identically on every page.

**Dark mode was recomputed, not inherited.** Light ink is a warm brown and the light band is
spruce, so every dark value had to move with them. The page ground is a warm near-black
(`#1A1411`), never a neutral grey, or paper and band would read as two different sites.
Tomato is too dark to be ink on a dark ground, so it lifts to a warm ember `#FF8A5C` (7.9:1)
and keeps the same job. Marigold is unchanged in both schemes: it is a surface, carrying the
same ink. Every pair above was computed, not eyeballed.

`_layouts/default.html`'s two `theme-color` metas moved with the palette (`#2A1912` light,
`#1A1411` dark). Left alone they would have put black browser chrome above a cream page, which
is the first thing a phone shows.

### R3.2 The pet signal, in two places

**The hero panel.** `assets/images/hero-pets.png` is the old site's own dog-and-cat cut-out,
cropped to its bounding box and colour-quantised: 298 KB down to **56 KB**, which matters
because the traffic channel is mobile Pinterest and this is the LCP element. It sits on a flat
marigold block. The art is a cut-out with no ground of its own, so the marigold *is* the
composition; on a white panel it would float.

It runs **first in source order**, so on a phone the dog and the cat are the first thing under
the masthead and the site says "pets" before a word of it is read. It takes the aside's column
on desktop and stretches full height, which is also what keeps R2.4 defect 4 (the dead
top-right quadrant) from coming back.

The aside's three-row fact list is **gone rather than moved**. It restated three lines the
"how we choose" band states below with the same numbers, and keeping it would have squeezed
the art into half a column for no new information.

**The wordmark's mark.** Higher leverage than the hero, because it sits above the fold on all
49 post pages plus `/dogs/`, `/cats/` and the static pages, where hero art never appears.
Round 2's lime square said nothing. It is a paw now, drawn as a CSS `mask` over a tomato
ground so it takes the palette's colour and recolours in dark mode for free. The footer
wordmark carries the same paw in marigold.

### R3.3 Defects found by rendering and looking (each fixed)

1. **The paw masked as a solid square.** `pawprint-nav.png` has an alpha channel, but the
   alpha is its rounded *tile*, not the paws: the paws are dark ink painted on an opaque
   marigold ground. Masking with it produced a tomato rounded rectangle. `paw-mask.png` is
   derived from that file with alpha taken from the paw ink instead, opened with a min/max
   filter to drop the antialiasing speckle at the tile's corners.
2. **Two paws were mush at wordmark size.** The mark renders at roughly 20px. The derived
   mask keeps one paw, not the pair.
3. **The hero panel composed differently at 1280 and at 1440.** A negative right margin ran
   it to the screen edge at 1280, slicing the dog through the body, while at 1440 (where the
   wrap is centred) it stopped short. Same rule, two compositions. The panel now ends flush
   with the container's right gutter, identical at every desktop width.
4. **The pet band floated below a strip of paper on a phone.** `.lede` carried 2.4rem of top
   padding from round 2, when its first child was text. With the panel first it has to sit
   flush under the masthead, so that padding moved onto `.lede-head`.
5. **Two hardcoded `rgba(242, 239, 230, .2)` hairlines inside the band** were round 2's bone
   colour, left behind by a token-only swap. They are `--rule-night` now.

### R3.4 What was rendered and actually looked at

Home at 390 and at 1280 full-page light, 390 and 1440 viewport light, 390 full-page dark
including the band and footer, plus a 390 dark crop of the masthead and hero. An article at
390 light. `/about/` at 390. `/dogs/` at 768.

**Not covered, stated rather than implied:** dark mode on `/dogs/`, `/cats/`, the article and
the static pages; 320px; any browser other than this machine's Chromium; iOS and Android type
rendering; and a real Jekyll build, which this machine still cannot run (see 0). The renderer
has no JavaScript, so the filter axes and "show more" were reviewed as code, never seen
working. R2.5's warning still applies: these images show every entry, where a real browser
shows 12 and a paging button.

### R3.5 Carried forward, still open

Everything in R2.6 is unchanged and still open: the orphaned `_includes/hero-graphic.html`, no
404 page, em-dashes reaching entry faces from `_posts` front matter, no thumbnail step in the
publishing pipeline, and internal notes still static-copied to the live site. Nothing is
committed to `main` and nothing is pushed. A push here is Class 3 (see 0). New this round:

1. `assets/images/happy-pets.png` (298 KB) is now unused by the site. `hero-pets.png` is
   derived from it and `og-image.png` is a byte-identical copy of it, so it is kept rather
   than deleted, but nothing references it.
2. **`og-image.png` was not touched.** It is the old share card and it no longer matches the
   site's palette. That is a share-surface decision, not a layout one, and it is the obvious
   place to start if the next job is traffic: the marigold pet panel is a far more shareable
   card than a bare cut-out on white.

### R3.6 Commands

    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r3-home-short --limit 6
    ./.venv/Scripts/python.exe scripts/preview_render.py home --dark --limit 5 --name r3-home-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py post --slug best-dog-pools --name r3-post
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug about --name r3-about
    ./.venv/Scripts/python.exe scripts/preview_render.py dogs --name r3-dogs

then render by bare name with `mcp__html-render__render_html_page` and **open the PNG and look
at it.** Every defect in R3.3 was found that way; none were visible in the markup.
