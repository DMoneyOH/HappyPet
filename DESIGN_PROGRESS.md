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
| `--hot` | tomato `#C6381B` | **Ink only.** Live filter state, and the running numerals as a 2px outline | 4.8:1 on paper as solid ink. A hairline outline renders well below its colour's ratio, so the numerals are decorative ordinal, never body text |
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
2. **`.lede-strip` is now redundant on the home page and was deliberately left
   alone.** Its round 2 job was putting photography on the first screen without
   rebuilding the card grid. The pet panel does that job now, so the strip is a
   third photo band in one screen showing the same six products the index repeats
   at larger size 400px below. Removing it is the right call on the merits and it
   is not this round's call to take: it deletes structure the Director kept. Flagged
   for his decision.
3. **`og-image.png` was not touched.** It is the old share card and it no longer matches the
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

---

## Round 4 — the brown, one type family, and a dark mode that is actually dark (2026-09-22)

Round 3 was reviewed live by the Director. **The direction survives again** — structure,
scale, hairlines, radius 0, two filter axes, the marigold pet panel, all unchanged. Seven
items came back; six were real and one was a non-defect he was warned off "fixing".

### R4.1 The brown was NOT where the brief guessed, and that is the useful finding

The dispatch's own hypothesis was the spruce band or something computed from it. Rendering
at 1280 and looking says otherwise: **the spruce band is fine and reads green.** The brown
was `--ink: #2A1912`, a chocolate. It is the page's text colour, so it was never going to be
judged as a text colour — it was judged as the **full-width slab across the top of the
page** (`.mast-strip`, `background: var(--ink)`) and as the 90px display headline directly
under it. Two of the largest areas on the first screen, both chocolate, on cream.

So the fix is the ink family, not the band. Ink now comes off the band's own spruce:

| Token | Round 3 | Round 4 | On paper |
| --- | --- | --- | --- |
| `--ink` | `#2A1912` chocolate | `#12271C` spruce-black | 14.4:1 |
| `--ink-2` | `#5A463C` | `#4B5B50` | 6.6:1 |
| `--ink-3` | `#7C6558` | `#616F64` | 4.8:1 |
| `--rule` / `--rule-2` | `rgba(42,25,18,…)` | `rgba(18,39,28,…)` | — |
| `--on-accent` | `#2A1912` | `#12271C` | 9.0:1 on marigold |

`--paper`, `--accent`, `--hot` and `--night` are untouched. The page now runs cream /
marigold / tomato / spruce, with the dark areas reading as deep evergreen. Every value
above was computed, not eyeballed.

**Where a token-only swap would have left brown behind** — this is R3.3 defect 5 repeating,
so it was grepped for rather than trusted: two `rgba(42, 25, 18, …)` hairline literals,
`--rule-hard`, `--focus`, `--on-accent` (which the dark block does *not* override, so it was
feeding brown ink onto every marigold button in dark mode), and **both `theme-color` metas
in `_layouts/default.html`**. That last one is the only brown that survives every render on
this machine: it paints the phone's browser chrome above the page, which is the first thing
a mobile visitor sees. Now `#12271C` light, `#1A2521` dark. **Not verifiable here — no
phone.**

### R4.2 One type family. Georgia is gone.

The Director likes the home page's type and wanted it everywhere. The divergence was one
token: `--font-prose: Georgia` carried `.standfirst` and `.prose`, which is most of an
article page and none of the home page, so the two templates genuinely looked like two
sites. `--font-prose` is now `var(--font-display)`. Grepped first — no component rule
hardcodes `Georgia`, so the token swap is complete.

This also **retires a risk round 2 raised and could never see**: on Android the serif fell
back to Noto Serif, a low-contrast humanist face that renders nowhere on this machine, so
every preview here judged the good case. There is now nothing left to fall back to.

**Retuned by looking, not by arithmetic:** the grotesque sets wider and carries more
x-height, so the 820px article column ran to roughly 85 characters a line. `.prose` is now
`1.04rem`, `line-height: 1.62`, and capped at `68ch`.

**The cap is on the text blocks, not on `.prose`** — and the first attempt got that wrong in
a way only a desktop render showed. Capping the container looked perfect at 390, where the
cap never binds. At 1280 the section hairlines are `border-top` on `.prose h2`, so they
stopped 60px short of the header rule and the purchase panel directly above them while
everything else still ran the full column: R2.4 defects 3 and 5 all over again. Rules,
tables and media now keep the full column and only the reading measure is capped, which is
also what keeps R2.4 defect 8's table fix intact.

### R4.3 "index" → "all the reviews", one name in all four places

He read "index" as cold and tacky. It surfaced in four user-facing slots, and three warm
synonyms for one destination would be worse than one cold name, so all four now say the
same thing: the hero button ("See all reviews"), the section heading ("all the reviews"),
the article breadcrumb and the footer link ("All reviews").

**The id moved with it, `index` → `reviews`, and that fixed a live defect nobody had
found:** `search.md` has linked to `/#reviews` in four places since round 2 and that anchor
never existed. All four were dead links. The `.index` CSS class names are not user-facing
and were left alone.

### R4.4 Footer categories are now real, and the destinations were checked

The eight links under "For dogs" and "For cats" were invented shelf headings — "Beds &
crates", "Scratchers & trees" — every one pointing at `/dogs/` or `/cats/`. Shelf labels for
a catalogue that does not exist.

Ground truth came from `_posts` front matter, not from `refill_products.py`'s
`VALID_CATEGORIES`: that list has 25 entries and is aspirational, with several categories
holding one post or none. The real tally is 23 categories across 49 posts. The eight chosen
are the top four per species: dog-health 8, dog-toys 4, dog-beds 3, dog-travel 3;
cat-litter 3, cat-food 3, cat-toys 2, cat-scratching 2.

**Each destination was computed, not assumed.** They link to `/search/?q=<label>`, and
`search.md`'s filter requires every 3+ character query term to appear in a post's `title` or
`tags` — categories are indexed but do not satisfy that filter on their own. So a
plausible-sounding label can land on an empty results page: `pet feeding`, `pet tech`,
`dog training` and `cat carriers` all return **zero**, and "Cat scratchers" returns zero
where "Cat scratching" returns two. Every one of the eight was checked against the real
front matter — including `detectSpecies`'s pre-filter, which drops off-species posts before
the term test — and returns at least two reviews. **Changing the wording of one without
re-running that check silently breaks it** — the comment in `default.html` says so.

The same four invented labels on `search.md`'s empty state were replaced from the same
verified set.

### R4.5 Dark mode now reads as dark. Three causes, not one.

Round 3's dark scheme inverted its tokens and still looked like the light page. Looking at
the render rather than the token block, the palette was the *smallest* of three causes:

1. **`.mast-strip` was `background: var(--ink); color: var(--paper)`.** Inverting those put
   a full-width **cream** slab across the top of the dark page. It has its own
   `--strip-bg` / `--strip-ink` pair now.
2. **`--tile` was `#F6EEE2`** — a light cream frame and border around all 49 photos.
3. **The white in every product shot is in the JPG's own pixels, not in CSS padding.**
   Checked rather than assumed: all 49 thumbnails are 800×600 with pure-white corners, and
   `.entry-media` is `4/3` with `object-fit: contain`, so recolouring the tile alone would
   have left the page a field of white rectangles. That is why the palette theory alone was
   never going to fix this.

So product photography is knocked back by `--img-dim` (`.78` in dark, `1` in light, so the
rule is inert in the light scheme) and restored on hover and focus. Kept mild deliberately:
a dim product photo is a real cost on a page whose job is selling. **The hero pet art is
explicitly excluded** — it sits on marigold in both schemes and is brand signal, not a
product shot. The page ground also moved off warm brown-black onto a deep green-black
(`#121A16`) so it belongs to the same family as the band, and `--night` lifted to `#20493A`
so the band still separates from the page.

### R4.6 The hero panel: both options were built and looked at

His framing was "depends what the background becomes". The background did not become
anything — `--accent` marigold was never the complaint and did not change — so the question
reduced to cut-out-on-marigold versus the art getting its own ground.

First, the asset was checked for the obvious cause: a 298 KB → 56 KB colour-quantised
cut-out is exactly the thing that grows a matte fringe. It has one, but **the fringe is dark,
not white** (the semi-transparent edge pixels quantised to near-black), and on marigold it
reads as an outline rather than a halo. So there is no defect here to fix.

Variant B was built anyway and rendered at 1280
(`80-workspace\scarlett\pages\r4-home-heroB.html`, kept so he can look): the art on its own
cream ground inside the marigold panel. It is clearly worse. The pets shrink to a small
floating photo in a field of marigold, the cut-out's legs get cropped by the box, and it
reintroduces a card, which lock 1 forbids everywhere else on the page. **Variant A stands
unchanged**, and it stands because it was compared, not because it was already there.

### R4.7 Item 7 — the 6-of-49 preview, correctly left alone

Confirmed as a static-preview artifact and **not touched**. `--limit` exists so the sections
below the index fit under the renderer's clip; the real page shows 12 plus a paging button.

### R4.8 What was rendered and actually looked at

Home at 1280 viewport and 1280 full-page light, and 390 full-page dark including the band
and the footer. An article at 1280 (twice — the second time to check the measure fix) and
390, light, and at 390 dark. `/about/`, `/contact/` and `/search/` at 390. `/dogs/` at 768,
`/cats/` at 390. The hero B variant at 1280. Every judgement above came from opening the
PNG, and two of them — the brown and the prose cap — contradicted what had been reasoned
out first.

**Not covered, stated rather than implied:** dark mode on `/dogs/`, `/cats/` and the static
pages; 320px; the `theme-color` browser chrome, which no render on this
machine can show; iOS and Android type rendering; any browser other than this machine's
Chromium; and a real Jekyll build, which this machine still cannot run (see 0). The renderer
has no JavaScript, so the filter axes, "show more", and the `?q=` prefill on `/search/` were
reviewed as code and never seen working — the eight footer queries were verified against
front matter by script, which is not the same as watching the page return results.

### R4.9 Carried forward, still open

Everything in R2.6 and R3.5 is unchanged and still open: the orphaned
`_includes/hero-graphic.html`, no 404 page, em-dashes reaching entry faces from `_posts`
front matter, no thumbnail step in the publishing pipeline, internal notes static-copied to
the live site, the now-unused `assets/images/happy-pets.png`, the redundant `.lede-strip`,
and `og-image.png` still off-palette (now further off it — it was made for the brown).
Nothing is committed to `main` and nothing is pushed. A push here is Class 3 (see 0). New
this round:

1. **`about.md`'s "What we cover" list is the aspirational taxonomy**, in the same shape the
   footer just came off — "Cat scratchers, trees, and furniture" and five more, several of
   which have no published review. It is a statement of intent in body copy rather than a
   set of links, so it is defensible where the footer was not, and it is out of this item's
   scope. Flagged rather than changed.
2. **A post's own front matter claims testing the site does not do.** One example seen while
   reading categories: `description: "We tested the top litters…"`. R2.3 deliberately titled
   the trust band "how we choose" rather than "how we test" to avoid exactly that exposure,
   and the generator is undoing it one post at a time. `_posts/` is out of scope; this
   belongs to whoever owns `generate_posts.py`.

### R4.10 Commands

    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r4-home-short --limit 6
    ./.venv/Scripts/python.exe scripts/preview_render.py home --dark --limit 5 --name r4-home-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py post --slug best-dog-pools --name r4-post
    ./.venv/Scripts/python.exe scripts/preview_render.py post --dark --slug best-dog-pools --name r4-post-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug about --name r4-about
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug contact --name r4-contact
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug search --name r4-search
    ./.venv/Scripts/python.exe scripts/preview_render.py dogs --name r4-dogs
    ./.venv/Scripts/python.exe scripts/preview_render.py cats --name r4-cats

then render by bare name with `mcp__html-render__render_html_page` and **open the PNG and
look at it.** R4.1 and R4.5 were both diagnosed that way, and in both cases the render
contradicted the theory that had been written down first.

---

## Round 5 — a bright white page on Chewy's profile, and a footer that stops repeating itself (2026-09-22)

Round 4 was reviewed live by the Director. **The direction survives again** — structure,
scale, hairlines, radius 0, two filter axes, the marigold pet panel, one type family, all
unchanged. Two items came back.

### R5.1 "Light and dark read practically the same" — the cream was the cause, not the dark scheme

Round 4 fixed three real causes of a dark mode that looked light and the schemes still read
as near-twins. Looking at the two renders **side by side at the same width**, which is what
the complaint is actually about, the remaining cause is on the light side: **cream is a
mid-light surface.** Against a `#121A16` ground it gives a smaller gap than it looks like on
its own, and it also caps how bright the light scheme can ever get. So the light page was
never going to "lighten up" while the ground stayed cream, and the separation was never going
to open up while the light half sat in the middle.

**The reference given was Chewy, and the honest answer to "how literally" is: its brightness
profile completely, its hue selectively.** What came over whole — white ground `#FFFFFF`,
near-black ink `#121212`, a real light-grey band `#EDEFF2`, no muted tone anywhere. What came
over as a decision — the blue, as the site's **structural** hue rather than its identity:

| Token | Round 4 | Round 5 | Job |
| --- | --- | --- | --- |
| `--paper` | cream `#FFF3E2` | **white** `#FFFFFF` | the page |
| `--paper-2` | `#FFFBF3` | **`#EDEFF2`** | the grey band: photo plates, sticky bar, **footer** |
| `--ink` | `#12271C` spruce-black | **`#121212`** | 18.7:1 on paper (was 14.4) |
| `--ink-2` / `--ink-3` | `#4B5B50` / `#616F64` | `#4A5057` / `#5E656D` | 8.2:1 / 5.9:1 |
| `--brand` | — | **`#1C49C4`** NEW | strip ground, live filter state, focus |
| `--strip-bg` | `#12271C` | **`#1C49C4`** | the full-width band across the top of every page |
| `--night` | `#16402F` spruce | **`#10276B`** navy | the dark band. White 13.8:1, marigold 7.9:1 |
| `--accent` | `#FFB627` | **unchanged** | surface only — and louder on white than on cream |
| `--hot` | `#C6381B`, two jobs | `#C6381B`, **one** | the wordmark paw, and nothing else |

**Why not a blue site.** His ask was "lighten this up, use that color scheme", not "become
blue", and Chewy's own blue is chrome — its colour comes from product photography, not from
its palette. Marigold keeps every surface job it had, because the warm pet panel is this
site's identity and is the one thing in four rounds he has never asked to change. Blue does
what blue does at Chewy: the top band, the live state, focus.

**Three places where a token rename would have quietly made a design decision, so each was
decided on purpose:**

1. **The paw stays tomato.** `.brand i` was `var(--hot)`. The paw sits above the fold on all
   49 post pages and every static page — the most-repeated brand element on the site — and
   letting the strip's blue reach it through a rename would have turned the one warm pet
   signal in the chrome cold as a side effect. Marigold is not available: it is a surface, and
   at 1.7:1 on white it would be a smudge.
2. **The 49 running numerals lose their colour entirely**, `var(--hot)` → `var(--ink-3)`.
   In blue they would read as 49 links on a page that now references a retailer's blue; in
   tomato they are a third saturated hue arguing with the strip and the panel, on a scheme
   whose whole fault was muddle. Neutral, the mark does its actual job — ordinal position —
   and marigold keeps the lead numeral.
3. **The footer moves onto the grey band** rather than the page. On cream, `--paper-2` was a
   hair lighter than `--paper` and a footer in it would have been invisible; on white it is
   Chewy's own grey band and it gives the bottom of the page a real edge with no card and no
   extra rule.

**Re-derived rather than swapped, because this exact class of bug has bitten twice
(R3.3 defect 5, R4.1):** `--rule` and `--rule-2` (spruce-derived rgba), `--rule-night` (was
derived from the *old cream paper* and would have left a warm hairline on a navy band),
`--rule-hard`, `--focus`, `--on-night-2` (`#B9CFC2` is green-tinted and belonged to spruce;
now `#C3D0EE`), `--lift`, and **both `theme-color` metas** — `#1C49C4` light to match the
strip, `#0E1116` dark to match the page. Grepped afterwards for every retired hex and every
old rgba literal: none survive outside a comment. **The metas are still not verifiable here —
no phone.**

**Dark was recomputed, not inverted.** Ground `#0E1116`, a cool near-black in the blue's
family; strip `#132A5E`, dark and still recognisably the brand blue; band `#16306B`; brand
ink lifts to `#7FA6FF` (7.9:1 — the light blue is 2.4:1 on this ground and would have been
unreadable). Round 4's three dark causes were each re-checked in the render after the
rewrite, since a palette rewrite is exactly what regresses them: the strip is dark, the tiles
are dark, `--img-dim` still knocks the JPG whites back.

**`--img-dim` retuned by looking: .78 → .72.** It was set against round 4's lighter ground;
on this one the photo strip was the brightest thing on the page. Still mild on purpose — a
dim product photo is a real cost on a page whose job is selling.

### R5.2 The footer stopped saying the species twice

"Dog health" under a "For dogs" heading prints the species in every line. The labels are bare
categories now — Health, Toys, Beds, Travel / Litter, Food, Toys, Scratching.

**Every `href` is byte-identical and must stay that way.** R4.4's note said "the label IS the
query"; that is no longer true, and the tempting follow-through — shortening `?q=dog+health`
to `?q=health` — would break it. `search.md`'s filter requires every 3+ character term to
appear in a post's title or tags, and `detectSpecies` pre-filters off-species posts, so the
species word in the query is load-bearing even though it is now off screen. The comment in
`default.html` was rewritten to say exactly that. **"Toys" appearing in both columns is
correct**, not a duplicate to tidy: two different queries.

**`search.md`'s four category links were deliberately left alone.** They sit in a single
mixed-species list under "or start from a category" with no species heading, so there is no
heading to be redundant with and the species word is the only thing distinguishing them. The
rule is "don't repeat the heading", not "strip species everywhere".

**Flagged, not changed:** the **"Reviews"** column has the same shape — "All reviews", "Dog
products", "Cat products", "Search reviews" under `<h3>Reviews</h3>`. It is arguably the same
redundancy, but his instruction scoped to the species headings and this is a footer he
reviewed live, so it is his call rather than scope creep.

### R5.3 What was rendered and actually looked at

**Both schemes opened side by side at the same width**, which is the check that matches the
complaint — home at 390 viewport, 390 full-page, 1280 viewport and 1280 at 1400px tall, light
and dark each. An article at 390 light and 390 dark. `/search/` and `/about/` at 390 light,
`/dogs/` at 768 light. The dim retune was made after looking at the dark 1280 and confirmed in
a second render. `/cats/` at 390 **dark**, specifically to check the demoted running numerals
at close to native resolution in the harder scheme: `--ink-3` `#8D96A0` at 2px outline on
`#0E1116`, which is the lowest-contrast pairing the demotion creates. It reads as a quiet
ordinal, not as mush — the risk lock 2 names (an outline renders well below its colour's
ratio) was checked rather than assumed.

Concretely, how they differ now: white page / royal-blue strip / black ink, against near-black
page / navy strip / white ink. The marigold hero panel and the marigold buttons are the only
elements that are visually identical between the two, which is the intent — they are the
brand constant.

**Round 5 touched exactly three files:** `assets/css/style.css`, `_layouts/default.html` and
this file. `_layouts/home.html` and `search.md` also show as modified in git — that is round 4
work, dirty before this round began.

**Not covered, stated rather than implied:** dark mode on `/dogs/` and the static
pages; 320px; the `theme-color` browser chrome, which no render on this machine can show; iOS
and Android type rendering; any browser other than this machine's Chromium; and a real Jekyll
build, which this machine still cannot run (see 0). The renderer has no JavaScript, so the
filter axes, "show more" and the `?q=` prefill were reviewed as code and never seen working —
the eight footer queries are unchanged from round 4's verified set, so they were not re-run.

**One trade carried forward, visible in every dark render:** the product shots' whites live in
the JPG's own pixels, so even at `--img-dim: .72` the lead entry's photo is the largest light
area on the dark page. Fixing it properly means regenerating 49 thumbnails with a transparent
or dark ground, which is an asset-pipeline change, not a CSS one.

### R5.4 Carried forward, still open

Everything in R2.6, R3.5 and R4.9 is unchanged and still open: the orphaned
`_includes/hero-graphic.html`, no 404 page, em-dashes reaching entry faces from `_posts` front
matter, no thumbnail step in the publishing pipeline, internal notes static-copied to the live
site, the unused `assets/images/happy-pets.png`, the redundant `.lede-strip`, `about.md`'s
aspirational "What we cover" list, and a post's front matter claiming testing the site does
not do. **`og-image.png` is now further off-palette again** — it was made for the brown, then
stranded by the spruce, and the site is now white-and-blue. Nothing is committed to `main` and
nothing is pushed. A push here is Class 3 (see 0).

### R5.5 Commands

    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r5-home-short --limit 6
    ./.venv/Scripts/python.exe scripts/preview_render.py home --dark --limit 6 --name r5-home-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py post --slug best-dog-pools --name r5-post
    ./.venv/Scripts/python.exe scripts/preview_render.py post --dark --slug best-dog-pools --name r5-post-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug about --name r5-about
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug search --name r5-search
    ./.venv/Scripts/python.exe scripts/preview_render.py dogs --name r5-dogs
    ./.venv/Scripts/python.exe scripts/preview_render.py cats --name r5-cats

then render by bare name with `mcp__html-render__render_html_page` and **open the PNG and look
at it — this round, open BOTH schemes at the same width and compare them**, because the fault
being fixed was comparative and each scheme looked fine inspected on its own.

---

## Round 6 — light mode only, and a corrected read of what Chewy actually looks like (2026-09-22)

The Director reviewed round 5 live and scoped this round himself: **stop working on dark mode,
put all attention on making light mode ready.** So this round is not a balanced pass over two
schemes. Dark mode was deliberately NOT worked on — not overlooked. What that meant in practice
is set out in R6.5, including the two places where a shared CSS rule made it impossible to
change light without dark moving with it, and one dark-visible bug that was found and
deliberately left alone.

### R6.1 The Chewy reference was re-read from real pages, and round 5's conclusion was too narrow

Round 5 derived its Chewy read from the site's CSS variables and concluded "blue is the
structural hue — top strip, the dark band, live filter state, focus." Mid-round, two real
screenshots of live Chewy pages arrived (a homepage promo section and a category page) and were
opened and looked at. That conclusion does not survive them.

What the actual pages do:

- **The blue is concentrated in exactly one place** — the top header bar, with a white wordmark
  and a white search field in it. It is bold, saturated, and it is the only strong blue on the
  screen.
- **Everything below that bar is white.** No navy slab, no second blue area, no blue chrome.
- **The variety comes from LIGHT TINTED BANDS used as section grounds** — a pale blue panel
  (~`#DCEBFB`) behind a row of photo tiles, a pale mint promo banner, each carrying near-black
  text. These are large, quiet and do a job; they are not decoration on a card.
- **Small saturated badges** carry the loud colour instead: a crimson "Deal" chip on a card
  corner.
- **Product photos sit on their own light grounds**, warm neutral rather than pure white.

So the accurate sentence is not "blue is the structural hue". It is: **white is the site's
colour; one strong blue header; light tinted bands for variety; small saturated badges for
emphasis.** Round 5 added one blue element to an otherwise samey palette and called that the
Chewy read. This round makes the page bright and varied the way the reference actually is.

### R6.2 The navy claims band became a pale blue one

The `how we choose` band is the single largest area of flat colour on the site, which is why
every round has had an opinion about it — near-black in round 2, spruce in round 4, navy in
round 5. A full-width deep navy slab is precisely the structural blue chrome below the header
that real Chewy pages do not have, and on a page whose stated fault was "not bright enough" it
was the thing holding the brightness down.

| Token | Round 5 (light) | Round 6 (light) | Job |
| --- | --- | --- | --- |
| `--night` to **`--band`** | navy `#10276B` | **pale blue `#E3EDFC`** | the one section band |
| `--on-night` to **`--on-band`** | `#FFFFFF` | **`#121212`**, 16.3:1 on the band | band ink |
| `--on-night-2` to **`--on-band-2`** | `#C3D0EE` | **`#434B57`**, 8.0:1 | band secondary ink |
| `--rule-night` to **`--rule-band`** | `rgba(255,255,255,.24)` | **`rgba(18,18,18,.20)`** | hairlines in the band |
| `--paper-3` | — | **`#FCF3E4` NEW** | the footer, warm cream |
| `--paper-2` | grey band: plates, bar, footer | **`#EDEFF2`, value unchanged, job narrowed** | cool grey UI chrome only: sticky buy bar, empty plate |

**The tokens were renamed, not just revalued, and that was the point.** A token called `--night`
holding a pale blue is the same class of quiet lie this file has been bitten by twice (R3.3
defect 5, R4.1): the next person to read it makes a decision on a name that is no longer true.
`--band` is honest in both schemes. Grepped afterwards — `--night`, `--on-night` and
`--rule-night` survive only inside historical comments that describe what earlier rounds did.

**The page now has three light grounds, each with a job**, which is what "bright and varied"
actually means here: white page, pale blue claims band, warm cream footer. The cream also puts a
warm note at the bottom of the page answering the marigold hero at the top, so the variety comes
from the site's own identity rather than from copying a second Chewy hue.

**The cream footer is an original call, not a reference read, and is flagged as such.** Neither
screenshot shows Chewy's footer, so unlike the pale blue band there is no primary evidence
behind this one — the argument for it is the site's own marigold identity plus the reference's
general method of using more than one light ground. It is the second-largest new area on the
page. If only one of this round's two new grounds is kept, keep the band.

**Lock 2 was amended in the stylesheet header rather than quietly drifted from.** Round 5's rule
said saturated hue is AREA and never a pastel tint. That still holds for hue *on type* and for
coloured chips on cards. What is new is that a large section GROUND may be a light tint carrying
near-black ink. The distance back to the seven-pastel kids' template of the first pass is that
there is **one** such ground with a job, not five as decoration.

**The three trust numerals had to change, and they were not made neutral.** `.creed-num b` was
marigold ink. Marigold on pale blue is 1.4:1 — the band's three numbers, which are its whole
point, would have washed out completely. Rather than demoting them to grey, they now take the
page's own signature device: the headline's marigold highlight block, `--on-accent` ink on it at
10.7:1. Marigold stays a surface, which is lock 2 unchanged, and the trust proof is now the
loudest thing in the band, which is what a trust proof should be.

### R6.3 Three defects found by looking, each a round 1-5 artifact light mode had inherited

1. **A ladder rung with nothing on it, in the claims band.** `.creed-nums` closed itself with a
   `border-bottom` and the first claim below it drew its own `border-top`, so two hairlines ran
   1.6rem apart with empty space between them. Present since round 2 and in both schemes.
   Fixed by dropping the stats row's rule, **not** the first claim's: at 1024 the claims become
   two columns, so suppressing only the first claim's rule (the obvious fix, and the one tried
   first) left the top-left cell bare while the top-right cell kept its rule. That asymmetry was
   caught by rendering at 1280 after the "fix", not by reasoning about it.
2. **A one-line heading floating ~50px clear of its own body copy, desktop only.**
   `.creed-points li` is `display: grid` and stretched to its parent grid row's height, and its
   two auto rows stretched with it. Four claims read as eight loose fragments at 1280. Fixed
   with `align-content: start`. Invisible at 390, where the list is one column and every row is
   its own height.
3. **The same brand glyph in two different hues.** The footer paw was `var(--accent)` marigold
   while the masthead paw two screens up was `var(--hot)` tomato. Round 5 decided the masthead
   one on purpose and wrote down why — *"marigold is not available: it is a surface, and at
   1.7:1 on white it would be a smudge"* — and the footer was never brought into line, so it
   had exactly the smudge that decision rejected (1.6:1 on the old grey, no better on cream).
   Now tomato, matching the masthead.

### R6.4 One thing that looked like a defect and was not, and one reference detail not copied

**The hero photo strip stopping short of the right edge at 1280 is a preview artifact.**
`home.html` says the strip deliberately runs off the right edge; the 1280 render showed it
ending ~250px short, which reads as an unfinished row. The discriminating check was re-running
the preview at `--limit 9` instead of `--limit 6`: with the nine tiles the layout actually asks
for, the strip runs past the right edge exactly as intended. Nothing was changed. **Judging the
round 5 render alone would have produced a wrong "fix" here.**

**Chewy's warm-neutral photo backdrops were deliberately not copied.** Its product cards put
photos on a cream/beige ground. Every thumbnail here is a product cut out on an 800x600 JPG with
pure-white corners, so a tinted plate behind one shows a white rectangle floating inside a cream
box. `--tile` stays `#FFFFFF`. Fixing it properly means regenerating 49 thumbnails with a
transparent ground — an asset-pipeline change, the same trade already carried in R5.3.

### R6.5 Dark mode: deliberately not touched, and exactly where that was not possible

Per the Director's instruction, **no dark-mode design work was done.** The dark `@media` block
was edited only to carry the four token renames through at their round 5 values, plus the new
`--paper-3` set to `--paper-2`'s value so the dark footer does not move. Rendered and looked at
at 390 to confirm: the ground, the navy band, the strip and the footer are unchanged.

**Two shared rules could not be changed on one side only, and both are stated rather than
buried:**

1. **The band's numerals are now marigold blocks with black ink in dark too**, not marigold
   text. Scoping the treatment to light would have left the two schemes structurally different
   for no reason. Checked in the dark render: it reads well there.
2. **The ladder-rung fix applies to dark as well**, being the same hairline.

Neither is a refinement of dark mode; both are the unavoidable other half of a light-mode fix.

**One genuine dark-mode bug was found and deliberately left unfixed, as instructed — and it
falsifies two claims in R5.** `assets/css/style.css` lines **247-249** are stray prose sitting
*outside* a closed comment (a paragraph about the sticky buy bar whose opening delimiter was
lost in an earlier edit). CSS error recovery swallows the invalid prelude **and the rule that
follows it**, which is:

    .entry-media img, .lede-strip img, .verdict-media img {
      filter: brightness(var(--img-dim));
      transition: filter var(--ease);
    }

So **`--img-dim` has never applied to anything.** Verified, not inferred: those exact lines were
pasted byte-for-byte into a throwaway page with `--img-dim: .15` and rendered — the swatch stayed
pure white, while a control rule after it applied normally.

Consequences, stated plainly:

- **Light mode is unaffected.** `--img-dim` is `1` in the light scheme, so the dead rule is a
  visual no-op there. This is why it was left alone rather than fixed: repairing it would change
  dark's rendered appearance for the first time, in a round the Director scoped to light.
- **R5.1's "the product shots' whites are knocked back by `--img-dim`" and R5.3's "`--img-dim`
  retuned by looking: .78 to .72" are both false.** The retune changed a number in a rule that
  does not run. Every dark render in rounds 3-5 was of undimmed product photography, which also
  means "dark mode still looks light" was being diagnosed against a broken control.
- **The fix is deleting three lines.** Whoever picks up the next dark round should delete
  247-249 first, then re-judge `--img-dim` from scratch — `.72` was tuned by eye against a photo
  strip that was never being dimmed, so it is a guess, not a measurement.

### R6.6 What was rendered and actually looked at

Light, this round: home at 390 full-page, 320 viewport, 1280 viewport and 1280 full-page; the
claims band and the footer each cropped at **native resolution** at both 390 and 1280, because
the earlier rounds' judgements of the footer were made from a 140px-wide downscale and the paw
hue defect is invisible at that size. An article at 390 light and 1280 light. `/about/` and
`/contact/` at 390 light. The defect-3 asymmetry and the defect-2 heading gap were both found by
looking at a 1280 crop *after* a first fix, and a second pass was rendered and looked at to
confirm each.

**Which renders are pre-change and which are post-change, because it matters here.** The
light-mode survey that found the three defects was run on an `r6base-*` build — the round 5 CSS,
before any edit this round. That survey covered `/search/` at 390, `/dogs/` at 768, `/contact/`
and `/privacy-policy/` at 390, home at 320, and the post page at 1280. **The footer and the band
both changed after it**, so the only pages whose new cream footer and pale band were actually
looked at are **home (390 and 1280) and `/about/` and `/contact/` (390)**. `/search/`, `/dogs/`,
`/cats/` and `/privacy-policy/` have not been seen with the round 6 palette. They share one
footer partial and one stylesheet, so the risk is low — but low is not seen, and saying so is
cheaper than a future round trusting a claim this file did not earn.

Dark, this round: home at 390 full-page, band cropped at native resolution — a **regression
check only**, not a design review.

**Round 6 touched exactly two files: `assets/css/style.css` and this one.** Everything else
showing as modified in git is round 4 and round 5 work, dirty before this round began.

**Not covered, stated rather than implied:** dark on anything but the home page; the
`theme-color` metas, which no render on this machine can show and which still name the blue
strip and the near-black dark page (both correct — neither changed); iOS and Android type
rendering; any browser other than this machine's Chromium; a real Jekyll build, which this
machine still cannot run. The renderer has no JavaScript, so the filter axes, "show more" and
the `?q=` prefill were reviewed as code and never seen working. **No contrast pair was measured
by eye — every ratio in R6.2 was computed.**

### R6.7 Carried forward, still open

Everything in R2.6, R3.5, R4.9 and R5.4 is unchanged and still open. Three of those are
light-visible and worth naming again with locations, because this round looked straight at them
and left them alone on purpose:

- **Long dashes reach entry faces from `_posts` front matter — 12 posts, and the count splits.**
  The full list, every hit in a `description:` field, scanned and written to a UTF-8 file rather
  than read off a console (a first pass crashed on an encode error mid-list and would have gone
  into this record with a filename missing):
  - **Em-dash, 10:** `2026-04-04-best-automatic-cat-feeder.md`,
    `2026-04-04-best-cat-litter-odor-control.md`, `2026-04-04-best-cat-scratching-posts.md`,
    `2026-04-04-best-dog-beds-large-breeds.md`, `2026-04-04-best-dog-collars-small-breeds.md`,
    `2026-04-04-best-no-pull-dog-harness.md`, `2026-04-04-best-pet-water-fountain.md`,
    `2026-04-04-best-puppy-training-pads.md`, `2026-04-20-best-cat-beds.md`,
    `2026-05-23-best-cat-tree-large.md`.
  - **En-dash, 2:** `2026-04-16-best-calming-treats-dogs.md`,
    `2026-05-18-best-dog-nail-grinder.md`.
  - **Unspaced em-dash, 2** — the specific AI tell, and a subset of the ten above:
    `2026-04-20-best-cat-beds.md` (`beds—find`) and `2026-05-23-best-cat-tree-large.md`
    (`style—without`).

  These are **published** posts on a site with an enabled autopublish cron, so rewriting twelve
  live descriptions is a content decision, not a design one, and it was not taken unilaterally.
  Worth noting for whoever does take it: the generator is still producing them, so scrubbing the
  twelve without fixing `generate_posts.py` buys one clean pass and then drifts back.
- **`about.md`'s "What we cover" list is still aspirational** — seven categories, several with
  no published review behind them. Changing what the site claims to cover is a business
  statement, not a CSS fix.
- **The sticky buy bar is cool grey `--paper-2` while the footer is now warm cream.** Judged and
  left: the bar is transient floating chrome and has to read as *not the page*. Worth a look if
  the cream starts reading as the site's second ground rather than just the footer's.

`og-image.png` remains off-palette — made for the brown, stranded by the spruce, then by the
white-and-blue, and now the page has a pale band and a cream footer too. Nothing is committed to
`main` and nothing is pushed. A push here is Class 3 (see 0).

### R6.8 Commands

    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r6-home-short --limit 2
    ./.venv/Scripts/python.exe scripts/preview_render.py home --name r6-home9 --limit 9
    ./.venv/Scripts/python.exe scripts/preview_render.py home --dark --limit 2 --name r6-home-dark
    ./.venv/Scripts/python.exe scripts/preview_render.py post --slug best-dog-pools --name r6-post
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug about --name r6-about
    ./.venv/Scripts/python.exe scripts/preview_render.py page --slug search --name r6-search
    ./.venv/Scripts/python.exe scripts/preview_render.py dogs --name r6-dogs

then render by bare name with `mcp__html-render__render_html_page` and open the PNG and look at
it. **This round, crop the band and the footer to NATIVE resolution before judging them** — a
full-page phone render is downscaled roughly 1:1.8, and both layout defects fixed this round are
invisible at that size.
