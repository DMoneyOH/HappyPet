---
layout: default
title: Search Reviews
permalink: /search/
---

<section class="search-page">
  <div class="search-hero">
    <h1>Search Reviews</h1>
    <p>Find the perfect product review for your dog or cat.</p>
    <div class="search-box-wrap">
      <input type="text" id="search-input" placeholder="Try &quot;dog bed&quot; or &quot;flea prevention&quot;..." autocomplete="off" autofocus />
      <span class="search-icon">🔍</span>
    </div>
  </div>
  <div class="search-results-wrap">
    <p id="search-status" class="search-status"></p>
    <ul id="search-results" class="search-results-list"></ul>
  </div>
</section>

<script src="https://cdnjs.cloudflare.com/ajax/libs/lunr.js/2.3.9/lunr.min.js"></script>
<script>
(function () {
  var idx, docs = [];

  fetch('{{ site.baseurl }}/search.json')
    .then(function(r){ return r.json(); })
    .then(function(data){
      docs = data;
      idx = lunr(function () {
        this.ref('id');
        this.field('title', { boost: 10 });
        this.field('tags',  { boost: 5  });
        this.field('categories', { boost: 3 });
        this.field('excerpt');
        data.forEach(function(d){ this.add(d); }, this);
      });
      var q = new URLSearchParams(window.location.search).get('q');
      if (q) { document.getElementById('search-input').value = q; runSearch(q); }
    });

  function runSearch(query) {
    var status = document.getElementById('search-status');
    var list   = document.getElementById('search-results');
    list.innerHTML = '';
    if (!query || query.trim().length < 2) { status.textContent = ''; return; }
    var results = idx ? idx.search(query + '~1') : [];
    if (results.length === 0) {
      status.textContent = 'No results for "' + query + '". Try a different term.';
      return;
    }
    status.textContent = results.length + ' result' + (results.length > 1 ? 's' : '') + ' for "' + query + '"';
    results.forEach(function(r){
      var doc = docs.find(function(d){ return d.id === parseInt(r.ref); });
      if (!doc) return;
      var li = document.createElement('li');
      li.className = 'search-result-item';
      li.innerHTML =
        '<a href="' + doc.url + '" class="search-result-link">' +
          '<span class="search-result-species">' + (doc.species === 'dog' ? '🐶' : doc.species === 'cat' ? '🐱' : '🐾') + '</span>' +
          '<div class="search-result-text">' +
            '<strong class="search-result-title">' + doc.title + '</strong>' +
            '<p class="search-result-excerpt">' + doc.excerpt + '</p>' +
          '</div>' +
        '</a>';
      list.appendChild(li);
    });
  }

  document.getElementById('search-input').addEventListener('input', function(){
    runSearch(this.value);
  });
})();
</script>
