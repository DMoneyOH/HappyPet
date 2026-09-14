---
layout: default
title: Cat Product Reviews | Happy Pet Product Reviews
description: In-depth reviews of the best cat products - litter, scratchers, feeders, carriers and more. Honest picks for every budget.
permalink: /cats/
---
{%- assign cat_only = site.posts | where: "species", "cat" -%}
{%- assign cat_both = site.posts | where: "species", "both" -%}
{%- assign cat_posts = cat_only | concat: cat_both -%}
<section class="section">
  <div class="wrap">
    <div class="section-head">
      <h1>for cats</h1>
      <span class="count">{{ cat_posts | size }} reviews</span>
    </div>
    <div class="entries">
      {% if cat_posts.size == 0 %}
      <div class="empty">
        <b>Cat reviews are on the way</b>
        <span>We are writing in-depth guides right now. Check back shortly.</span>
      </div>
      {% else %}
        {% for post in cat_posts %}
          {% if forloop.index == 1 %}
            {% include post-card.html post=post rank=forloop.index variant="major" eager=true %}
          {% elsif forloop.index <= 3 %}
            {% include post-card.html post=post rank=forloop.index variant="wide" eager=true %}
          {% elsif forloop.index <= 7 %}
            {% include post-card.html post=post rank=forloop.index eager=true %}
          {% else %}
            {% include post-card.html post=post rank=forloop.index %}
          {% endif %}
        {% endfor %}
      {% endif %}
    </div>
  </div>
</section>
