---
layout: default
title: Search Reviews
permalink: /search/
---

<section class="piece">
  <div class="wrap piece-inner">
    <h1>Search reviews</h1>
    <p class="standfirst">Find the right review for your dog or cat.</p>

    <form class="find" role="search" onsubmit="return false;">
      <label class="skip" for="search-input">Search reviews</label>
      <input type="search" id="search-input" autocomplete="off" autofocus
             placeholder="Try &quot;dog bed&quot; or &quot;flea prevention&quot;">
    </form>

    <p id="search-status" class="find-status" role="status" aria-live="polite"></p>
    <ul id="search-results" class="find-results"></ul>

    {%- comment -%}
      The empty state. An autofocused box over a blank page tells a visitor
      nothing about what is in here, and these links also carry the page for
      anyone whose search script never loads.
    {%- endcomment -%}
    <div class="find-suggest" id="search-suggest">
      <h2>Or start from a category</h2>
      <div class="find-links">
        <a href="{{ site.baseurl }}/dogs/">Dog reviews</a>
        <a href="{{ site.baseurl }}/cats/">Cat reviews</a>
        <a href="{{ site.baseurl }}/#reviews">Beds &amp; crates</a>
        <a href="{{ site.baseurl }}/#reviews">Feeding</a>
        <a href="{{ site.baseurl }}/#reviews">Toys</a>
        <a href="{{ site.baseurl }}/#reviews">Care &amp; training</a>
      </div>
      <p><a class="btn" href="{{ site.baseurl }}/">Browse all reviews</a></p>
    </div>
  </div>
</section>

<script src="https://cdnjs.cloudflare.com/ajax/libs/lunr.js/2.3.9/lunr.min.js"></script>
<script>
(function () {
  var idx, docs = [];

  // Species keyword maps
  var DOG_WORDS  = ['dog','dogs','puppy','puppies','canine','pup','pups'];
  var CAT_WORDS  = ['cat','cats','kitten','kittens','feline'];

  function detectSpecies(query) {
    var q = query.toLowerCase().split(/\s+/);
    var hasDog = q.some(function(w){ return DOG_WORDS.indexOf(w) > -1; });
    var hasCat = q.some(function(w){ return CAT_WORDS.indexOf(w) > -1; });
    if (hasDog && !hasCat) return 'dog';
    if (hasCat && !hasDog) return 'cat';
    return null; // no filter
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  fetch('{{ site.baseurl }}/search.json')
    .then(function(r){ return r.json(); })
    .then(function(data){
      docs = data;
      idx = lunr(function () {
        this.ref('id');
        this.field('title',      { boost: 10 });
        this.field('tags',       { boost: 5  });
        this.field('categories', { boost: 3  });
        this.field('excerpt');
        data.forEach(function(d){ this.add(d); }, this);
      });
      var q = new URLSearchParams(window.location.search).get('q');
      if (q) { document.getElementById('search-input').value = q; runSearch(q); }
    });

  function runSearch(query) {
    var status  = document.getElementById('search-status');
    var list    = document.getElementById('search-results');
    var suggest = document.getElementById('search-suggest');
    list.innerHTML = '';
    var searching = !!(query && query.trim().length >= 2);
    if (suggest) suggest.hidden = searching;
    if (!searching) { status.textContent = ''; return; }

    var speciesFilter = detectSpecies(query);
    var raw = idx ? idx.search(query) : [];

    // Apply species pre-filter then require title/tag relevance, cap at 6
    var queryTerms = query.toLowerCase().split(/\s+/).filter(function(w){ return w.length > 2; });
    var results = raw.filter(function(r){
      var doc = docs.find(function(d){ return d.id === parseInt(r.ref); });
      if (!doc) return false;
      // Species filter
      if (speciesFilter && doc.species !== speciesFilter && doc.species !== 'both' && doc.species) return false;
      // Require ALL query terms (3+ chars) to appear in title or tags — not just one
      var titleTags = ((doc.title || '') + ' ' + (doc.tags || '')).toLowerCase();
      return queryTerms.length === 0 || queryTerms.every(function(t){ return titleTags.indexOf(t) > -1; });
    }).slice(0, 6);

    if (results.length === 0) {
      status.textContent = 'No results for "' + query + '". Try a different term.';
      return;
    }
    status.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') + ' for "' + query + '"'
      + (speciesFilter ? ' (' + speciesFilter + 's only)' : '');

    results.forEach(function(r){
      var doc = docs.find(function(d){ return d.id === parseInt(r.ref); });
      if (!doc) return;
      var species = doc.species === 'dog' ? 'Dogs' : doc.species === 'cat' ? 'Cats' : '';
      var li = document.createElement('li');
      li.className = 'find-hit';
      li.innerHTML =
        '<a href="' + escapeHtml(doc.url) + '">'
        + (species ? '<span class="chip chip--plain">' + species + '</span>' : '')
        + '<strong>' + escapeHtml(doc.title) + '</strong>'
        + '<span>' + escapeHtml(doc.excerpt) + '</span>'
        + '</a>';
      list.appendChild(li);
    });
  }

  document.getElementById('search-input').addEventListener('input', function(){
    runSearch(this.value);
  });
})();
</script>
