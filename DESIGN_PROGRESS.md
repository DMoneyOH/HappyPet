# HappyPet — visual/UX redesign, working notes

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
2. `/dogs/`, `/cats/`, `/about/`, `/contact/`, `/privacy-policy/` and `/search/` have been
   converted but only `about` and `search` have been rendered and looked at so far.
3. No 404 page exists.
4. Post front matter written by `generate_posts.py` contains em-dashes, which now show on
   card faces through `post.description`. `_posts/` is out of scope for this work, so this
   is a note for whoever owns the generator, not something changed here.
5. Nothing is committed to `main` and nothing is pushed. A push here is Class 3 (see §0).
